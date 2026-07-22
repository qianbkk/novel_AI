"""backend/tests/test_character_card_defaults_2026_07_23.py — 验证问题 #9 修复

stage_characters schema 校验失败时降级补默认值，不抛 RuntimeError。
"""
from __future__ import annotations
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.worldbuild.stages import _fill_missing_card_defaults


def test_fill_missing_personality_summary():
    card = {"personality": {"tags": ["a"]}}  # 缺 summary
    out = _fill_missing_card_defaults(card, "test", "主角")
    assert "summary" in out["personality"]
    assert len(out["personality"]["summary"]) >= 10
    assert "summary" in out["personality"]["summary"]  # 提示里含 summary


def test_fill_missing_catchphrase_lines():
    card = {"catchphrase": {}}
    out = _fill_missing_card_defaults(card, "x", "配角")
    assert "lines" in out["catchphrase"]
    assert isinstance(out["catchphrase"]["lines"], list)
    assert len(out["catchphrase"]["lines"]) > 0


def test_fill_missing_arc_required_fields():
    card = {"arc": {}}
    out = _fill_missing_card_defaults(card, "x", "配角")
    assert out["arc"]["start_state"]
    assert out["arc"]["catalyst"]
    assert out["arc"]["end_state"]


def test_fill_all_8_segments():
    card = {}  # 全空
    out = _fill_missing_card_defaults(card, "x", "反派")
    for seg in ["basic", "appearance", "personality", "background",
                "abilities", "catchphrase", "props", "arc"]:
        assert seg in out, f"missing {seg}"


def test_does_not_mutate_input_top_level():
    """顶层 dict 引用独立（caller 不被改）。"""
    card = {"personality": {}}
    out = _fill_missing_card_defaults(card, "x", "y")
    # 输出是独立 dict 引用
    assert out is not card
    # 输出 personality 含 summary
    assert "summary" in out["personality"]


if __name__ == "__main__":
    test_fill_missing_personality_summary()
    test_fill_missing_catchphrase_lines()
    test_fill_missing_arc_required_fields()
    test_fill_all_8_segments()
    test_does_not_mutate_input()
    print("all passed")
