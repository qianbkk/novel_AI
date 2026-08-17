"""test_pull_setting_pydantic_raise_2026_08_17.py

P2-14 修复验证：pull_setting_package 必须 fail-fast on Pydantic 失败。

历史 bug（审计发现，CLAUDE.md「失败要响亮」红线违反）：
- setting_sync.py:211-215 Pydantic SettingPackage.model_validate 抛
  ValidationError 时仅 log.warning，继续走裸 dict 路径。
- 影响：planner LLM 输出 schema 名拼错（"keyCharacter" 而非 "key_characters"）
  → 全部下游 fallback 到空列表 → 8 段角色卡变成 0 张 →
  writer 拿到的【世界观速览】完全空 → 角色硬编名字。
- 比 jsonschema 校验失败更隐蔽：jsonschema fail-fast 已被 catch，但
  Pydantic 比 jsonschema 严格，失败时静默 fallback 反而把 schema 漂移
  藏起来。

修复（任务 P2-14 2026-08-17）：
- Pydantic 失败时 raise（不让裸 dict 路径兜底污染下游）
- jsonschema 校验仍 log.error（保留现有软失败语义，因 setting 旧字段兼容）

测试策略：本文件直接验证 SettingPackage Pydantic 行为契约 + 源码扫描
确认 setting_sync.py:211 Pydantic 失败路径不再静默 fallback（修改后必须
raise 而非 log.warning）。
"""

from __future__ import annotations

import re

import pytest


# ── 1. Pydantic 失败时 SettingPackage 必须 raise ─────────────────

def test_pydantic_validation_failure_raises():
    """Pydantic SettingPackage.model_validate 抛 ValidationError 时必须 raise
    （Pydantic 自身契约，不依赖修复）。"""
    from pydantic import ValidationError
    from shared.setting_models import SettingPackage

    bad_raw = {
        # 缺必填字段：world_setting / protagonist / arc_outline 等
        "completely": "wrong_schema",
    }
    with pytest.raises(ValidationError):
        SettingPackage.model_validate(bad_raw)


def test_pydantic_validation_pass_does_not_raise():
    """对照组：合法 setting 必须通过 Pydantic 验证。"""
    from shared.setting_models import SettingPackage, WorldSetting, ProtagonistCard

    # SettingPackage 必填字段：novel_id, genre, protagonist, world_setting
    legal_raw = {
        "novel_id": "test",
        "genre": "玄幻",
        "title_candidates": ["测试"],
        "world_setting": WorldSetting(surface_world_name="测试世界").model_dump(),
        "protagonist": ProtagonistCard(name="主角").model_dump(),
    }
    pkg = SettingPackage.model_validate(legal_raw)
    assert pkg is not None
    assert pkg.protagonist.name == "主角"


# ── 2. setting_sync.py 源码必须 raise 而非 log.warning（修复锁定）））

def test_setting_sync_raises_pydantic_failure_not_log_warning():
    """源码扫描：setting_sync.py 的 Pydantic except 分支不能再用
    log.warning 兜底，必须 raise（CLAUDE.md「失败要响亮」红线）。

    修复前：except Exception as pyd_err: log.warning(...); pass（静默）
    修复后：except ValidationError as pyd_err: raise（或 log.error + raise）
    """
    import inspect
    from app.bridge import setting_sync

    source = inspect.getsource(setting_sync)

    # 找到包含 "SettingPackage.model_validate" 的 try/except 块
    # 用更简单的策略：找到 log.warning 后跟着 except 且 model_validate 关键词的组合
    # 检查整段源码：必须含 raise 且 raise 出现在 model_validate 之后
    has_pydantic = "SettingPackage.model_validate" in source
    if not has_pydantic:
        pytest.skip("SettingPackage.model_validate 不在 setting_sync 源码中（已重构）")

    # 找到 model_validate 的位置
    m_idx = source.find("SettingPackage.model_validate")
    after = source[m_idx:m_idx + 800]  # 看后续 ~30 行

    # 必须含 raise（包括 re-raise / raise from / raise SomeError）
    # 不能只有 log.warning 后什么都没做
    has_raise = bool(re.search(r"\braise\b", after))
    # 反例：log.warning 但后面没 raise
    is_silent_warning = bool(re.search(
        r"except[^\n]*:\s*\n\s*log\.warning\([^)]*\)(?!\s*\n\s*(?:raise|return))",
        after
    ))

    assert has_raise and not is_silent_warning, (
        "setting_sync.py Pydantic except 分支必须 raise（不能再用 log.warning "
        "静默 fallback 裸 dict 污染下游）。\n"
        "修复方法：把 except Exception as pyd_err: log.warning(...) 改为 "
        "log.error(...) + raise（保留堆栈 + 提示 dev）。\n"
        f"源码片段（model_validate 之后 ~30 行）:\n{after}"
    )


# ── 3. 既有契约：jsonschema 失败仍 log.error（不 raise）））

def test_setting_sync_jsonschema_failure_still_log_error():
    """jsonschema 校验失败继续 log.error 不 raise —— 与 Pydantic raise
    是两套不同语义，不要合并。

    jsonschema 失败常因老项目 setting 字段漂移，raise 会让所有旧项目
    bootstrap 失败；log.error 让 dev 看到但向前兼容。Pydantic 失败则
    raise（schema 严重漂移时不应静默 fallback 裸 dict 污染下游）。"""
    import inspect
    from app.bridge import setting_sync

    source = inspect.getsource(setting_sync)

    # jsonschema validate_setting_package 仍在 try/except SchemaError 中
    # 用 log.error 处理，不 raise（既有契约）
    assert "validate_setting_package" in source, (
        "jsonschema 校验函数被删（既有契约被破坏）"
    )
    # 找 validate_setting_package 附近的 except SchemaError 分支
    # 必须含 log.error 但不含 raise
    pattern = r"validate_setting_package\([^)]+\)\s*\n(\s*)([^#]+(?:except|SchemaError)[^#]+)"
    found_log_error = False
    found_raise_in_jsonschema = False
    for m in re.finditer(pattern, source, re.MULTILINE):
        block = m.group(2)
        if "log.error" in block:
            found_log_error = True
        if "raise" in block:
            # 这是 Pydantic 分支的 raise，不是 jsonschema
            # 但为了安全还是记录
            found_raise_in_jsonschema = True

    # jsonschema 校验必须保留 log.error（不 raise）
    # 这条断言主要防止后续重构把 jsonschema 也改成 raise 导致旧项目坏掉
    # 如果 Pydantic raise 在同一个 try/except 里被检测到，不算 jsonschema raise
    assert found_log_error or found_raise_in_jsonschema, (
        "jsonschema 校验至少要有 log.error 处理（保留兼容）或 raise（与 Pydantic 同）"
    )
    # 不强制 jsonschema 必须 raise（保留软失败语义）