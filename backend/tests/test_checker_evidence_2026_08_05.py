"""test_checker_evidence_2026_08_05.py

2026-08-05 修复（清单 issue #6 工程未接通）：

  之前 checker.Evidence dataclass 写好了 Evidence() helper，但
  run_checker 不接 evidence，orchestrator 也未拼 evidence。
  本测试保证 evidence 真正下沉到 score_chapter 内部 prompt，并不会
  因为后续重构再悄悄丢参。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_run_checker_passes_evidence_to_all_three_models():
    """full 模式下 evidence 必须透传给主评 + 两路交叉评。"""
    from engine.agents import checker as ck
    captured = []

    def fake_score(text, task, *, evidence=None, agent_name):
        captured.append({"agent_name": agent_name, "evidence": evidence})
        return ({"dimensions": {"pacing": 7}, "specific_feedback": "ok"}, 0.0)

    ev = ck.Evidence(
        setting_text="表面世界：远环殖民地",
        last_chapter_ending="林渊被神秘人跟踪",
        recent_events=[{"chapter": 5, "summary": "主角觉醒"}],
        foreshadowing_to_resolve=["星环遗物", "林家血仇"],
    )

    with patch.object(ck, "score_chapter", side_effect=fake_score):
        _result, _cost = ck.run_checker(
            "正文内容", {"chapter_number": 6, "chapter_role": "发展"},
            audit_mode="full", evidence=ev,
        )

    assert len(captured) == 3, f"full 模式三路评审均应被调；实际 {len(captured)}"
    agent_names = sorted(c["agent_name"] for c in captured)
    assert agent_names == ["checker_cross1", "checker_cross2", "checker_main"]
    for entry in captured:
        assert entry["evidence"] is ev, (
            "每个 score_chapter 必须拿到同一 evidence 对象；"
            f"actual={entry['evidence']!r}"
        )


def test_run_checker_lite_passes_evidence_to_single_model():
    """lite 模式（重写后质检）也要传 evidence。"""
    from engine.agents import checker as ck
    captured = []

    def fake_score(text, task, *, evidence=None, agent_name):
        captured.append({"agent_name": agent_name, "evidence": evidence})
        return ({"dimensions": {"pacing": 6.5}}, 0.0)

    ev = ck.Evidence(setting_text="x", last_chapter_ending="y")

    with patch.object(ck, "score_chapter", side_effect=fake_score):
        ck.run_checker("text", {"chapter_number": 1}, audit_mode="lite", evidence=ev)

    assert len(captured) == 1
    assert captured[0]["agent_name"] == "checker_main"
    assert captured[0]["evidence"] is ev


def test_score_chapter_evidence_renders_into_user_prompt():
    """Evidence 块应该真的进入 score_chapter 的 user_prompt（之前走 fallback 的
    '无前文可比对' 文本;现在 user_prompt 必须含有上章结尾 / 近期事件字样）。"""
    from engine.agents import checker as ck

    captured_prompt = {"value": None}

    def fake_router_call(self=None, *, agent_name, system_prompt, user_prompt, max_tokens, temperature):
        captured_prompt["value"] = user_prompt
        # 返合规 JSON 让 parse 不走 default
        return ('{"dimensions":{"pacing":6,"character_voice":6,"plot_logic":6,"consistency":6,"writing_naturalness":6,"hook_power":6},"specific_feedback":"x"}', 0.001)

    ev = ck.Evidence(
        setting_text="失落的世界设定",
        last_chapter_ending="林渊签订契约瞬间",
        recent_events=[{"chapter": 3, "summary": "主角觉醒"}],
        foreshadowing_to_resolve=["废土之夜"],
    )

    fake_router = MagicMock()
    fake_router.call.side_effect = fake_router_call

    with patch("engine.agents.checker.get_active_router", return_value=fake_router):
        ck.score_chapter("章节正文", {"chapter_number": 4, "chapter_role": "发展",
                                       "shuang_description": ""},
                          evidence=ev)

    prompt = captured_prompt["value"]
    assert "林渊签订契约瞬间" in prompt, (
        f"上章结尾必须进入 prompt 供 consistency 评分使用；actual={prompt[:300]}"
    )
    assert "废土之夜" in prompt, "近期应回收伏笔必须进入 prompt"
