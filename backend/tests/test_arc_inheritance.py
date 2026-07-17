"""跨弧摘要与未决剧情线继承回归测试。

覆盖三个目标：
  1. summarizer 弧末档案回灌 L2.hot.last_arc_summary
  2. summarizer unresolved_threads 回灌 L2.constraints.next_arc_incoming_threads（跨弧去重）
  3. outline prompt 注入上一弧档案（last_arc_summary）和跨弧继承剧情线
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


def test_summarizer_backflow_to_l2_hot():
    """summarize_arc 成功后，last_arc_summary 必须写入 L2.hot。"""
    from engine.agents.summarizer import summarize_arc

    arc = {"arc_id": 1, "arc_name": "觉醒"}
    arc_summary = {
        "arc_id": 1,
        "arc_name": "觉醒",
        "summary_100": "主角觉醒",
        "key_events": ["事件A", "事件B"],
        "unresolved_threads": ["未结的线"],
        "protagonist_growth": "主角从凡人变感债者",
    }
    fake_router = MagicMock()
    fake_router.call.return_value = (str(arc_summary).replace("'", '"'), 0.001)
    fake_l5 = {"arc_summaries": [], "compressed_history": "", "character_arcs": {}, "major_revelations": []}
    fake_l2 = {"hot": {}, "cold": {}, "constraints": {}, "meta": {}}
    with patch("engine.agents.summarizer.get_active_router", return_value=fake_router), \
         patch("engine.agents.summarizer.get_l5", return_value=fake_l5), \
         patch("engine.agents.summarizer.save_l5"), \
         patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        result, _ = summarize_arc(arc, [], {}, "test_novel")
        assert fake_l2["hot"].get("last_arc_summary") == arc_summary


def test_summarizer_unresolved_threads_backflow():
    """unresolved_threads 必须写入 next_arc_incoming_threads，按 desc 去重。"""
    from engine.agents.summarizer import summarize_arc

    arc = {"arc_id": 2, "arc_name": "觉醒"}
    arc_summary = {
        "arc_id": 2,
        "summary_100": "s",
        "key_events": [],
        "unresolved_threads": [
            {"desc": "陈昭疤的秘密"},
            "王德顺之死",
            {"desc": "陈昭疤的秘密"},  # 重复
        ],
    }
    fake_router = MagicMock()
    fake_router.call.return_value = (str(arc_summary).replace("'", '"'), 0.001)
    fake_l5 = {"arc_summaries": [], "compressed_history": "", "character_arcs": {}, "major_revelations": []}
    fake_l2 = {"hot": {}, "cold": {}, "constraints": {}, "meta": {}}
    with patch("engine.agents.summarizer.get_active_router", return_value=fake_router), \
         patch("engine.agents.summarizer.get_l5", return_value=fake_l5), \
         patch("engine.agents.summarizer.save_l5"), \
         patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        summarize_arc(arc, [], {}, "test_novel")
        incoming = fake_l2["constraints"]["next_arc_incoming_threads"]
        descs = [t["desc"] for t in incoming]
        assert "陈昭疤的秘密" in descs
        assert "王德顺之死" in descs
        # 去重：相同 desc 只出现一次
        assert descs.count("陈昭疤的秘密") == 1


def test_summarizer_incoming_threads_idempotent_across_calls():
    """第二次调用同 arc 时不应重复灌入相同 desc。"""
    from engine.agents.summarizer import summarize_arc

    arc = {"arc_id": 3}
    arc_summary = {
        "arc_id": 3, "summary_100": "s", "key_events": [],
        "unresolved_threads": [{"desc": "X"}],
    }
    fake_router = MagicMock()
    fake_router.call.return_value = (str(arc_summary).replace("'", '"'), 0.001)
    fake_l5 = {"arc_summaries": [], "compressed_history": "", "character_arcs": {}, "major_revelations": []}
    # 模拟已经存在的 incoming（不重复灌）
    fake_l2 = {"hot": {}, "cold": {}, "constraints": {
        "next_arc_incoming_threads": [{"desc": "X", "from_arc": 2, "status": "inherited"}],
    }, "meta": {}}
    with patch("engine.agents.summarizer.get_active_router", return_value=fake_router), \
         patch("engine.agents.summarizer.get_l5", return_value=fake_l5), \
         patch("engine.agents.summarizer.save_l5"), \
         patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        summarize_arc(arc, [], {}, "test_novel")
        incoming = fake_l2["constraints"]["next_arc_incoming_threads"]
        assert len(incoming) == 1
        assert incoming[0]["from_arc"] == 3


def test_summarizer_replaces_stale_incoming_threads():
    """next_arc 列表只代表最新弧的未决线，旧弧已消失的线应被清理。"""
    from engine.agents.summarizer import summarize_arc

    arc = {"arc_id": 4}
    arc_summary = {
        "arc_id": 4, "summary_100": "s", "key_events": [],
        "unresolved_threads": ["新未决线"],
    }
    fake_router = MagicMock()
    fake_router.call.return_value = (str(arc_summary).replace("'", '"'), 0.001)
    fake_l5 = {"arc_summaries": [], "compressed_history": "", "character_arcs": {}, "major_revelations": []}
    fake_l2 = {"hot": {}, "cold": {}, "constraints": {
        "next_arc_incoming_threads": [{"desc": "旧未决线", "from_arc": 2}],
    }, "meta": {}}
    with patch("engine.agents.summarizer.get_active_router", return_value=fake_router), \
         patch("engine.agents.summarizer.get_l5", return_value=fake_l5), \
         patch("engine.agents.summarizer.save_l5"), \
         patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        summarize_arc(arc, [], {}, "test_novel")

    incoming = fake_l2["constraints"]["next_arc_incoming_threads"]
    assert [item["desc"] for item in incoming] == ["新未决线"]


def test_outline_user_prompt_includes_last_arc():
    """outline prompt 在 last_arc_summary 存在时必须渲染「上一弧档案」块。"""
    from engine.agents.outline import _build_user_prompt

    arc = {"arc_id": 2, "arc_name": "深入", "arc_goal": "g", "estimated_chapters": 30,
           "arc_climax_description": "c", "emotion_curve": "上升",
           "new_characters_introduced": [], "arc_ending_state": ""}
    setting = {"protagonist": {"name": "陈昭"}, "key_characters": [],
               "power_system": {"levels": []}}
    memory = {
        "hot": {
            "protagonist_level": "觉醒", "protagonist_points": 100,
            "active_threads": [],
            "last_arc_summary": {
                "summary_100": "主角觉醒", "key_events": ["事件A"],
                "protagonist_growth": "成长为感债者",
                "unresolved_threads": ["伏笔X"],
            },
        },
        "constraints": {
            "next_arc_incoming_threads": [
                {"desc": "伏笔X继承", "from_arc": 1, "status": "inherited"},
            ],
        },
    }
    prompt = _build_user_prompt(arc, 31, setting, memory)
    assert "上一弧档案" in prompt
    assert "主角觉醒" in prompt
    assert "伏笔X继承" in prompt
    assert "跨弧继承剧情线" in prompt


def test_outline_user_prompt_works_without_last_arc():
    """首弧时 last_arc_summary 缺失，prompt 仍应正常构造（不抛错）。"""
    from engine.agents.outline import _build_user_prompt

    arc = {"arc_id": 1, "arc_name": "首弧", "arc_goal": "g",
           "estimated_chapters": 20, "arc_climax_description": "",
           "emotion_curve": "", "new_characters_introduced": [],
           "arc_ending_state": ""}
    setting = {"protagonist": {"name": "陈昭"}, "key_characters": [],
               "power_system": {"levels": []}}
    memory = {"hot": {"protagonist_level": "凡人", "active_threads": []},
              "constraints": {}}
    prompt = _build_user_prompt(arc, 1, setting, memory)
    assert "弧1「首弧」" in prompt
    assert "上一弧档案" not in prompt  # 首弧不应有上弧信息


def test_outline_user_prompt_accepts_structured_summary_items():
    from engine.agents.outline import _build_user_prompt

    arc = {"arc_id": 2, "arc_name": "续弧", "arc_goal": "g",
           "estimated_chapters": 20, "arc_climax_description": "",
           "emotion_curve": "", "new_characters_introduced": [],
           "arc_ending_state": ""}
    setting = {"protagonist": {"name": "陈昭"}, "key_characters": [],
               "power_system": {"levels": []}}
    memory = {"hot": {
        "active_threads": [],
        "last_arc_summary": {
            "summary_100": "摘要",
            "key_events": [{"event": "事件A"}],
            "unresolved_threads": [{"desc": "伏笔A"}],
        },
    }, "constraints": {}}

    prompt = _build_user_prompt(arc, 21, setting, memory)
    assert "事件A" in prompt
    assert "伏笔A" in prompt
