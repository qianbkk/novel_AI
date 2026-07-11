"""app/api/auth.py — 认证端点：register / login / me / change-password

Phase 4 多用户认证 API。
- /auth/register: 创建 user + 为已有 owner_id=NULL 的 Project backfill
- /auth/login: bcrypt 验签，签发 JWT
- /auth/me: 当前 user 详情（要 token）
- /auth/change-password: 修改密码（要 token）

设计原则（与 Phase 3 memo 一致）：
- 不引入 RBAC；只支持"多用户各自独立数据"。
- 不写权限模型。所以没有"删除 user"/"列出 user"等管理端点——
  这种 admin 操作属于"将来真要 SaaS 化时再说"的范畴。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from sqlalchemy.orm import Session

from ..auth import (
    get_current_user_required,
    hash_password,
    issue_token,
    reset_jwt_secret_cache,
    verify_password,
)
from ..database import SessionLocal, get_db
from ..logging_setup import get_logger
from ..models import Project, User

log = get_logger("novel_ai.auth_api")

router = APIRouter(prefix="/auth", tags=["auth"])


# ───────── Pydantic 模型 ─────────
class RegisterRequest(BaseModel):
    email: str = Field(..., description="唯一邮箱（仅 ASCII / 简单格式校验）")
    password: str = Field(..., min_length=8, max_length=128,
                          description="密码（最少 8 字符）")
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="秒数")
    user: "UserOut"


class UserOut(BaseModel):
    id: str
    email: str
    display_name: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


# ───────── 端点 ─────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """注册新 user 并自动签发 token。

    首次注册的特殊性：把 DB 里所有 owner_id=NULL 的 Project 都 backfill 给
    这个新 user，避免"我的老数据丢了"。后续注册不再动老数据。
    """
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email.split("@", 1)[1]:
        raise HTTPException(422, "email 格式不对")

    existing = db.query(User).filter_by(email=email).first()
    if existing:
        raise HTTPException(409, "邮箱已注册")

    # 评估"首次注册"语义：用 Project.owner_id IS NULL 计数判断
    unowned_count = db.query(Project).filter(Project.owner_id.is_(None)).count()
    is_first_user = db.query(User).count() == 0

    user = User(
        email=email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if is_first_user and unowned_count > 0:
        # backfill：首次注册的 user 自动拥有所有 owner_id=NULL 的 Project
        updated = db.query(Project).filter(
            Project.owner_id.is_(None)
        ).update({"owner_id": user.id})
        db.commit()
        log.info("register: first user backfilled %d project(s) (owner_id ← user.id)",
                 updated)

    token = issue_token(user.id)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=7 * 86400,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """登录：bcrypt 验签密码 → 签发 token。

    注意：始终返回"邮箱或密码不对"（不区分），防止用户枚举攻击。
    """
    email = payload.email.strip().lower()
    user = db.query(User).filter_by(email=email).first()
    # 故意调 verify_password 一次以拉齐 timing
    dummy_hash = "$2b$12$" + "x" * 53
    if not user or not verify_password(payload.password,
                                       user.password_hash or dummy_hash):
        log.warning("login failed for email=%s", email)
        raise HTTPException(401, "邮箱或密码不对")

    token = issue_token(user.id)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=7 * 86400,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user_required)):
    """当前 user 详情。"""
    return UserOut.model_validate(user)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """改密码。要 token + 旧密码。"""
    db_user = db.get(User, user.id)
    if not db_user or not verify_password(payload.old_password, db_user.password_hash):
        raise HTTPException(401, "旧密码不对")
    db_user.password_hash = hash_password(payload.new_password)
    db.commit()
    log.info("password changed for user=%s", user.id)
    return {"ok": True}


@router.post("/dev/reset-jwt-secret")
def dev_reset_jwt_secret():
    """仅 dev 模式可用：重置 JWT secret cache + 文件。

    测试场景需要"清空" token 状态时调用。生产模式（NOVEL_PRODUCTION=1）拒绝。
    """
    import os
    if os.environ.get("NOVEL_PRODUCTION") == "1":
        raise HTTPException(404, "not found")
    reset_jwt_secret_cache()
    return {"ok": True}


# ──────── admin helpers（Phase 4 预留，仅 dev 模式可用）───────
@router.get("/dev/_users")
def dev_list_users(db: Session = Depends(get_db)):
    """仅 dev：列出所有 user（测试用）。生产模式 404。"""
    import os
    if os.environ.get("NOVEL_PRODUCTION") == "1":
        raise HTTPException(404, "not found")
    users = db.query(User).order_by(User.created_at).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]
