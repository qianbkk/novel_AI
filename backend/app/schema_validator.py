"""
JSON Schema 单例加载器 + 校验工具（compat shim）。

为什么有这个文件：
  之前 planner.py 输出的 setting_package.json 和 setting_sync.py 消费的字段
  名漂移，导致 pull_setting 后 5 张表全空。修复后我们用 JSON Schema 草案
  把契约固化在 backend/schema/ 下，planner 输出前 validate，consumer 读取
  后 validate。任何"加字段"必须先改 schema 文件。

2026-07-25 抽离：实际实现已迁到 backend/shared/setting_schema.py（修 P0 双向
import）。本文件保留为 thin shim，所有公开 API 转发到 shared，避免破坏老代码。

用法：
  from app.schema_validator import validate_setting_package, validate_chapter_meta
"""
from __future__ import annotations

# 2026-07-25：转发到 backend/shared/setting_schema.py（修 P0 双向 import）。
# 本文件保留作为向后兼容 shim，app 内部统一改用 shared.setting_schema。
from shared.setting_schema import (  # noqa: F401
    SchemaError,
    get_chapter_meta_schema,
    get_character_card_schema,
    get_entity_relation_rich_schema,
    get_setting_package_schema,
    get_world_view_rich_schema,
    validate_chapter_meta,
    validate_character_card,
    validate_entity_relation_rich,
    validate_setting_package,
    validate_world_view_rich,
)

__all__ = [
    "SchemaError",
    "validate_setting_package",
    "validate_chapter_meta",
    "validate_world_view_rich",
    "validate_character_card",
    "validate_entity_relation_rich",
    "get_setting_package_schema",
    "get_chapter_meta_schema",
    "get_world_view_rich_schema",
    "get_character_card_schema",
    "get_entity_relation_rich_schema",
]
