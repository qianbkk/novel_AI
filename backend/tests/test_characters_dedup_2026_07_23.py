"""backend/tests/test_characters_dedup_2026_07_23.py — 验证问题 #4 修复

stage_characters 内部去重：同 stage 写入时同名 name 只保留第一条。
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.worldbuild.stages import stage_characters, _CHARACTERS_MOCK


def _valid_card():
    return _CHARACTERS_MOCK[0]["card"]




def test_stage_characters_dedup_by_name():
    """同名角色第二次出现应 skip（不抛异常）。"""
    db = MagicMock()
    db.flush = MagicMock()
    project = MagicMock()
    project.id = "test-project"
    ctx = {"project": project, "story_core": "", "plot_skeleton": [], "world_view": ""}

    payload = {
        "characters": [
            {"name": "林渊", "role": "主角", "card": _valid_card()},
            {"name": "林渊", "role": "主角（重复）", "card": _valid_card()},
        ]
    }

    async def run():
        with patch("app.worldbuild.stages.call_llm_json", return_value=payload):
            await stage_characters(ctx, db)

    asyncio.run(run())

    assert db.add.call_count == 1, f"db.add called {db.add.call_count} times, expected 1 (deduped)"
    assert len(ctx["characters"]) == 1, f"ctx[characters] len={len(ctx['characters'])}, expected 1"
    assert ctx["characters"][0]["name"] == "林渊"


def test_stage_characters_different_names_all_added():
    """不同 name 应该全部添加。"""
    db = MagicMock()
    project = MagicMock()
    project.id = "test-project"
    ctx = {"project": project, "story_core": "", "plot_skeleton": [], "world_view": ""}

    payload = {
        "characters": [
            {"name": "林渊", "role": "主角", "card": _valid_card()},
            {"name": "苏晚栀", "role": "配角", "card": _valid_card()},
            {"name": "孟浩", "role": "反派", "card": _valid_card()},
        ]
    }

    async def run():
        with patch("app.worldbuild.stages.call_llm_json", return_value=payload):
            await stage_characters(ctx, db)

    asyncio.run(run())

    assert db.add.call_count == 3
    assert len(ctx["characters"]) == 3


def test_stage_characters_mixed_dedup():
    """混合：3 个不同 + 2 个重复（共 5 个输入）→ 写入 3 条。"""
    db = MagicMock()
    project = MagicMock()
    project.id = "test-project"
    ctx = {"project": project, "story_core": "", "plot_skeleton": [], "world_view": ""}

    payload = {
        "characters": [
            {"name": "林渊", "role": "主角", "card": _valid_card()},
            {"name": "苏晚栀", "role": "配角", "card": _valid_card()},
            {"name": "林渊", "role": "主角dup", "card": _valid_card()},  # 重复
            {"name": "孟浩", "role": "反派", "card": _valid_card()},
            {"name": "苏晚栀", "role": "配角dup", "card": _valid_card()},  # 重复
        ]
    }

    async def run():
        with patch("app.worldbuild.stages.call_llm_json", return_value=payload):
            await stage_characters(ctx, db)

    asyncio.run(run())

    assert db.add.call_count == 3, f"expected 3 (deduped), got {db.add.call_count}"
    assert len(ctx["characters"]) == 3


if __name__ == "__main__":
    test_stage_characters_dedup_by_name()
    test_stage_characters_different_names_all_added()
    test_stage_characters_mixed_dedup()
    print("all passed")
