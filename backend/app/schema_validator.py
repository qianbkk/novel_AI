"""
JSON Schema 单例加载器 + 校验工具。

为什么有这个文件：
  之前 planner.py 输出的 setting_package.json 和 setting_sync.py 消费的字段
  名漂移，导致 pull_setting 后 5 张表全空。修复后我们用 JSON Schema 草案
  把契约固化在 backend/schema/ 下，planner 输出前 validate，consumer 读取
  后 validate。任何"加字段"必须先改 schema 文件。

用法：
  from app.schema_validator import validate_setting_package, validate_chapter_meta

  validate_setting_package(raw_dict)  # raises SchemaError
  validate_chapter_meta(raw_dict)      # raises SchemaError
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import jsonschema  # required at import time — fail-fast if missing

from .logging_setup import get_logger

log = get_logger("novel_ai.schema")

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

# 懒加载 + 缓存（避免每个请求都从磁盘读）
_setting_pkg_schema: dict | None = None
_chapter_meta_schema: dict | None = None
# ─── Phase 1：世界构建板块结构化 ───
_world_view_rich_schema: dict | None = None
_character_card_schema: dict | None = None
_entity_relation_rich_schema: dict | None = None


class SchemaError(ValueError):
    """schema 校验失败。把 jsonschema 的错误转成可读信息。"""
    def __init__(self, name: str, errors: list[dict]):
        self.name = name
        self.errors = errors
        bullets = [f"  - {'/'.join(str(x) for x in e['path'])}: {e['message']}"
                   for e in errors[:10]]
        super().__init__(f"{name} schema 校验失败 ({len(errors)} 处):\n" + "\n".join(bullets))


def _load(name: str) -> dict:
    p = _SCHEMA_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"schema file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def get_setting_package_schema() -> dict:
    global _setting_pkg_schema
    if _setting_pkg_schema is None:
        _setting_pkg_schema = _load("setting_package.schema.json")
    return _setting_pkg_schema


def get_chapter_meta_schema() -> dict:
    global _chapter_meta_schema
    if _chapter_meta_schema is None:
        _chapter_meta_schema = _load("chapter_meta.schema.json")
    return _chapter_meta_schema


# ─── Phase 1：世界构建板块结构化校验 ───
def get_world_view_rich_schema() -> dict:
    global _world_view_rich_schema
    if _world_view_rich_schema is None:
        _world_view_rich_schema = _load("world_view_rich.schema.json")
    return _world_view_rich_schema


def get_character_card_schema() -> dict:
    global _character_card_schema
    if _character_card_schema is None:
        _character_card_schema = _load("character_card.schema.json")
    return _character_card_schema


def get_entity_relation_rich_schema() -> dict:
    global _entity_relation_rich_schema
    if _entity_relation_rich_schema is None:
        _entity_relation_rich_schema = _load("entity_relation_rich.schema.json")
    return _entity_relation_rich_schema


def _check(data: Any, schema: dict, name: str) -> None:
    v = jsonschema.Draft7Validator(schema)
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    if errs:
        raise SchemaError(name, [
            {"path": list(e.absolute_path), "message": e.message} for e in errs
        ])


def validate_setting_package(data: Any) -> None:
    """校验 planner 输出。失败抛 SchemaError。"""
    _check(data, get_setting_package_schema(), "setting_package")


def validate_chapter_meta(data: Any) -> None:
    """校验 chapter meta。失败抛 SchemaError。"""
    _check(data, get_chapter_meta_schema(), "chapter_meta")


def validate_world_view_rich(data: Any) -> None:
    """校验 stage_world_basics 的 7 段世界观。失败抛 SchemaError。"""
    _check(data, get_world_view_rich_schema(), "world_view_rich")


def validate_character_card(data: Any) -> None:
    """校验 stage_characters 的角色卡。失败抛 SchemaError。"""
    _check(data, get_character_card_schema(), "character_card")


def validate_entity_relation_rich(data: Any) -> None:
    """校验 stage_relations 的富关系（强度 / 标签 / 演化 / 关键事件）。失败抛 SchemaError。"""
    _check(data, get_entity_relation_rich_schema(), "entity_relation_rich")


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
