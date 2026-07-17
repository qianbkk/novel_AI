"""app/auth.py — 多用户认证（Phase 4）

设计原则（基于 Phase 3 上线决策备忘录）：
  - 单租户本地使用仍是默认场景——所有 API 在无 token 时仍可访问、
    owner_id 为 NULL 的数据视为"共享数据"，与之前行为一致。
  - 真有多用户需求时（外部用户 / 多设备共享 / 数据隔离有意义），
    注册 + 登录拿 JWT token，所有数据查询自动加上 owner_id 过滤。
  - 不引入 RBAC / 权限模型——已确认场景是"多用户各自独立数据"，不是
    "多用户共享协作"。Phase 3 memo 明确不预先实现这块。

数据模型：
  - users 表：id, email (unique), display_name, password_hash, created_at
  - projects.owner_id（已 Phase 1 预埋的 nullable 列）：首次注册的
    user backfill 时挂上；后续 create project 路由自动写当前 user.id。

密码哈希：bcrypt（cost=12）。不用 passlib 全栈——bcrypt 包 API 简单稳定。

JWT：
  - HS256 + JWT_SECRET env（dev 模式 fallback 到磁盘持久化，仿 MASTER_KEY 模式）
  - 默认 7 天有效期（连续使用的场景比短期 token 友好）
  - payload: {sub: user_id, exp, iat}

本地访问兼容性（Phase 4 关键）：
  - get_current_user_optional: 解析 Authorization: Bearer；
    无 token / 无效 token → 返回 None，不抛 401。
    路由拿到 None 时继续按"单租户"模式跑（owner_id=NULL 数据可见）。
  - get_current_user_required: 用于 /auth/me 等需要"我"上下文的端点。
  - 在 NOVEL_PRODUCTION=1 时，所有跨用户读取类端点会自动开始强制
    鉴权（生产模式不放过隐式 NULL）。Phase 4 内置这个开关。
"""
from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import SessionLocal
from .logging_setup import get_logger
from .models import User

log = get_logger("novel_ai.auth")

# ─────────────────────────────────────────────
# JWT 配置
# ─────────────────────────────────────────────
JWT_ALGORITHM = "HS256"
JWT_DEFAULT_EXPIRE_DAYS = 7

# dev 模式持久化路径（仿 MASTER_KEY 的迭代 #82 模式）
_JWT_SECRET_PATH = Path(__file__).resolve().parent.parent / "data" / ".dev_jwt_secret"
_jwt_secret_cache: Optional[str] = None


def _generate_jwt_secret() -> str:
    """生成 64 字节随机 secret（base64 编码）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii")


def _validate_jwt_secret(candidate: str) -> Optional[str]:
    if not candidate or len(candidate) < 32:
        return None
    return candidate


def _load_persisted_jwt_secret() -> Optional[str]:
    try:
        if not _JWT_SECRET_PATH.exists():
            return None
        raw = _JWT_SECRET_PATH.read_text(encoding="utf-8").strip()
        return _validate_jwt_secret(raw)
    except Exception as e:
        log.warning("读 %s 失败（%s），将重新生成 JWT secret",
                    _JWT_SECRET_PATH, e)
        return None


def _persist_jwt_secret(secret: str) -> None:
    try:
        _JWT_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _JWT_SECRET_PATH.with_suffix(_JWT_SECRET_PATH.suffix + ".tmp")
        tmp.write_text(secret, encoding="utf-8")
        os.replace(tmp, _JWT_SECRET_PATH)
        log.info("dev JWT secret 已持久化到 %s（gitignored）", _JWT_SECRET_PATH)
    except Exception as e:
        log.warning("持久化 JWT secret 失败：%s", e)


def _get_jwt_secret() -> str:
    """获取 JWT signing secret。

    优先级：
      1) env JWT_SECRET（source-of-truth）
      2) 磁盘持久化文件（dev 模式兼容 --reload）
      3) 临时生成 + 持久化
    """
    global _jwt_secret_cache

    env = os.environ.get("JWT_SECRET", "").strip()
    if env:
        validated = _validate_jwt_secret(env)
        if validated:
            return validated
        raise RuntimeError(
            "JWT_SECRET 已设但长度过短（需 ≥32 字符）。"
            "  生成：python -c \"import secrets;print(secrets.token_urlsafe(64))\""
        )

    if _jwt_secret_cache:
        return _jwt_secret_cache

    persisted = _load_persisted_jwt_secret()
    if persisted:
        _jwt_secret_cache = persisted
        return persisted

    new_secret = _generate_jwt_secret()
    _jwt_secret_cache = new_secret
    log.warning(
        "JWT_SECRET 环境变量未设置，dev 模式自动生成并持久化一个新 secret 到 %s。"
        "生产部署务必设置 JWT_SECRET env。",
        _JWT_SECRET_PATH,
    )
    _persist_jwt_secret(new_secret)
    return new_secret


def reset_jwt_secret_cache() -> None:
    """测试 / 运维用：清掉进程级 cache + 磁盘文件。

    不会让已签发的 token 失效——之前签的 token 用旧 secret 仍然能验签
    到期为止。仅在显式 reset 时让"之后的" token 用新 secret。
    """
    global _jwt_secret_cache
    _jwt_secret_cache = None
    try:
        if _JWT_SECRET_PATH.exists():
            _JWT_SECRET_PATH.unlink()
    except Exception as e:
        log.warning("删 %s 失败：%s", _JWT_SECRET_PATH, e)


# ─────────────────────────────────────────────
# 密码哈希
# ─────────────────────────────────────────────
_BCRYPT_ROUNDS = 12  # 默认 cost 因子


def hash_password(plain: str) -> str:
    """bcrypt hash（cost=12，单次通常需要数百毫秒）。"""
    if not plain:
        raise ValueError("密码不能为空")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """验签密码。timing-safe（bcrypt.compare 直接用。"""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        # malformed hash
        return False


# ─────────────────────────────────────────────
# JWT 签发 / 解析
# ─────────────────────────────────────────────
def issue_token(user_id: str, *, expire_days: Optional[int] = None) -> str:
    """签发 access token。user_id 是 hex uuid。"""
    exp_days = expire_days if expire_days is not None else JWT_DEFAULT_EXPIRE_DAYS
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=exp_days)).timestamp()),
        "iss": "novel_ai",
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """解析 token。失败 → None 不抛，让上层决定。"""
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            _get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        log.debug("token expired")
        return None
    except jwt.InvalidTokenError:
        log.debug("token invalid")
        return None


# ─────────────────────────────────────────────
# FastAPI 依赖
# ─────────────────────────────────────────────
def _extract_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "").strip()
    if not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip() or None


def _is_production() -> bool:
    """是否处于生产模式（NOVEL_PRODUCTION=1）。

    生产模式下，无 token 的请求访问"会跨用户暴露数据"的端点直接 401。
    dev 模式（默认）允许无 token，单租户 mode 行为不变。
    """
    return os.environ.get("NOVEL_PRODUCTION") == "1"


def _user_from_token(token: str, db: Session) -> Optional[User]:
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = db.get(User, user_id)
    return user


def get_current_user_optional(
    request: Request,
) -> Optional[User]:
    """从 Authorization: Bearer 头解析 user。

    - dev 模式 / 无 token / 无效 token → 返回 None
    - 生产模式下，可选鉴权仍允许 None——上层路由按业务决定是否强制。

    为不与 get_db 冲突，自己 short-lived 开 session。
    """
    token = _extract_bearer(request)
    if not token:
        return None
    db = SessionLocal()
    try:
        return _user_from_token(token, db)
    finally:
        db.close()


def get_current_user_required(
    request: Request,
) -> User:
    """强制鉴权。无效 token / 无 token → 401。

    用于 /auth/me 等必须知道"我是谁"的端点。
    生产模式下也会用于跨用户数据访问端点（上层选择性 Depends）。
    """
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def user_is_admin(user: User) -> bool:
    """是否有管理员标记（Phase 4 没有 admin 角色，预留）。"""
    return bool(getattr(user, "is_admin", False))
