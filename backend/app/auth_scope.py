"""app/auth_scope.py — owner_id 过滤 / 跨用户隔离（Phase 4 多用户上线）

设计原则（与 Phase 3 memo + auth.py 一致）：
  - 单租户本地使用仍是默认：owner_id=NULL 的 Project 视为"未指定用户的数据"，所有登录/未登录 user 都可见。
  - 真多用户时：每个 user 只能读/写 owner_id == self.id 的数据。
  - 在 NOVEL_PRODUCTION=1 模式下：未登录 user 访问任何 project-scoped 端点直接 401，
    老 owner_id=NULL 的数据首次 register 时已经被 backfill 到第一个 user，所以
    不存在"共享数据"访问路径。这等于强制鉴权上线的开关。

helper 两种用法：
  - 函数式：require_owned_project(db, project_id, current_user) — 在路由里手动调
  - 依赖式：current_user_opt + 用 owner_filter 查询

行级过滤的 owner 子句：
  - current_user is None: 不加 owner 过滤（dev 模式可见所有数据）；
    production 模式下上层应已 401 拦截。
  - current_user present: WHERE owner_id = current_user.id OR owner_id IS NULL
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Query, Session

from .auth import User
from .models import Project


def is_production_mode() -> bool:
    """便捷函数：是否处于生产模式（fail-fast 鉴权）。"""
    return os.environ.get("NOVEL_PRODUCTION") == "1"


def owner_filter_clause(current_user: Optional[User]):
    """返回 SQLAlchemy 过滤子句：把"仅看自己 + NULL 共享数据"包好。

    用法：
        query = db.query(Project).filter(owner_filter_clause(user))
    """
    if current_user is None:
        # 未登录 → dev 模式可见全部；生产模式上层应已 401 拦过。
        return Project.owner_id.is_(None)  # 仅 NULL：等价于"未认领数据可见"
    from sqlalchemy import or_
    return or_(
        Project.owner_id == current_user.id,
        Project.owner_id.is_(None),  # 兼容历史数据
    )


def require_owned_project(
    db: Session,
    project_id: str,
    current_user: Optional[User],
) -> Project:
    """按 current_user + project_id 取 Project，且校验 owner 关系。

    三种分支：
      1. project 不存在 → 404
      2. dev 模式 + 未登录 → 允许任何 project（不验 owner）
         （owner_id 为空或非空都能看）
      3. dev 模式 + 已登录 → 仅看 owner_id == self.id 或 owner_id IS NULL
      4. production 模式 → 必须已登录 + owner_id 匹配（NULL 不放过）

    Returns:
        Project 实例

    Raises:
        HTTPException 404: project 不存在
        HTTPException 403: 跨用户访问
        HTTPException 401: production 模式下未登录
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    prod = is_production_mode()

    if current_user is None:
        if prod:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # dev 模式：未登录也允许看（兼容旧前端/旧 client）
        return project

    # 已登录：按 owner 校验
    owner = getattr(project, "owner_id", None)
    if owner is None:
        # owner 未认领：dev 模式允许；prod 模式下因为首个 user register 时
        # 已经 backfill，NULL 存在 = 数据损坏 → 403 不可读
        if prod:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "project has no owner (production mode refuses unowned data)",
            )
        return project

    if owner != current_user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "forbidden: not project owner",
        )

    return project
