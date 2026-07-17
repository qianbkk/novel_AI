"""backend/tests/test_tracker_phase1_retry.py — 一期修复回归测试

覆盖三个目标：
  1. tracker schema 简化后，下游字典访问对新旧字段都安全
     （不再硬读 new_world_events / new_constraints / new_facts / resolved_foreshadowing 等）
  2. parse 失败时自动 retry 一次（失败率从 ~96% 期望降到 < 30%）
  3. retry 仍然失败时仍然走原有的 meta 标记路径（fail-soft，不崩）

注：跑 tracker 真实 LLM 调用需要 MiniMax/DeepSeek key + 网络；这里用
mock router 测 retry 路径的分支逻辑。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


# ────────────────────────────────────────────────────────────
# 1. tracker schema 简化：下游读取安全
# ────────────────────────────────────────────────────────────

def test_tracker_runs_without_new_world_events_field():
    """schema 简化后，新版 tracker 输出不含 new_world_events。

    下游 run_tracker 对 updates.get("new_world_events") 的依赖必须容忍 None/缺字段。
    这里通过 mock router 让 tracker 收到一份「只有 4 个核心字段」的 JSON，
    验证 run_tracker 不会抛 KeyError 也不丢已存在的 closed_threads。
    """
    from engine.memory.manager import empty_l2
    from engine.agents.tracker import run_tracker

    # mock router: 第一次 parse 失败，第二次 reformat 后返回合规 JSON
    minimal_valid_json = json.dumps({
        "chapter_summary": "主角第一次签到成功",
        "character_states": {"陈昭": "觉醒签到系统"},
        "active_threads": ["主线: 签到系统觉醒"],
        "new_closed_threads": [],
    }, ensure_ascii=False)
    bad_json = "这是乱七八糟的输出 {无 [法] 解析"
    router = MagicMock()
    router.call.side_effect = [
        (bad_json, 0.001),                # 第一次：垃圾
        (minimal_valid_json, 0.001),      # 第二次（retry）：合规
    ]
    with patch("engine.agents.tracker.get_active_router", return_value=router), \
         patch("engine.agents.tracker.save_l2"):
        mem = empty_l2()
        task = {"chapter_number": 1, "main_characters": ["陈昭"],
                "chapter_goal": "主角觉醒签到系统"}
        text = "陈昭在午夜的出租屋收到系统提示。"
        new_mem, cost = run_tracker(text, task, mem, "test_novel")

    # retry 路径成功 → summary 应进入 hot.recent_summaries
    summaries = new_mem["hot"]["recent_summaries"]
    assert len(summaries) == 1, f"retry 应该写入 summary，实际={summaries}"
    assert "签到" in summaries[0]["summary"]
    # character_states 被记录
    assert new_mem["hot"]["character_states"].get("陈昭") == "觉醒签到系统"
    # meta 标记
    assert new_mem["meta"]["total_chapters_tracked"] == 1


def test_tracker_returns_empty_when_both_calls_fail():
    """两次调用都解析失败时，必须走 fail-soft 路径（meta 标记 + 不崩）。"""
    from engine.memory.manager import empty_l2
    from engine.agents.tracker import run_tracker

    router = MagicMock()
    router.call.side_effect = [
        ("garbage 1", 0.001),
        ("garbage 2", 0.001),
    ]
    with patch("engine.agents.tracker.get_active_router", return_value=router), \
         patch("engine.agents.tracker.save_l2"):
        mem = empty_l2()
        task = {"chapter_number": 7, "main_characters": ["陈昭"]}
        new_mem, cost = run_tracker("正文……", task, mem, "test_novel")

    # 双失败时不应崩
    assert new_mem["meta"]["total_chapters_tracked"] == 1
    # 失败次数被记录
    assert new_mem["meta"]["tracker_parse_failure_count"] >= 1
    # recent_summaries 仍是空（因为从来没拿到合法 JSON）
    assert new_mem["hot"]["recent_summaries"] == []


# ────────────────────────────────────────────────────────────
# 2. schema 简化 → 必填字段只剩 chapter_summary
# ────────────────────────────────────────────────────────────

def test_tracker_system_prompt_minimal_fields():
    """schema 简化回归：TRACKER_SYSTEM 必须明确「只填有变化的字段」。"""
    from engine.agents.tracker import TRACKER_SYSTEM

    # 必填提醒
    assert "chapter_summary" in TRACKER_SYSTEM
    assert "必填" in TRACKER_SYSTEM

    # 宁缺勿滥
    assert "宁缺勿滥" in TRACKER_SYSTEM or "没变化" in TRACKER_SYSTEM

    # 已删除的字段不应再作为必填提示
    assert "new_world_events" not in TRACKER_SYSTEM.split("【关键约束】")[0], (
        "new_world_events 不应在主字段列表中（已移到 closed_threads 等）"
    )
    # resolved_foreshadowing 仍保留但标注"仅本章明确回收"
    assert "resolved_foreshadowing" in TRACKER_SYSTEM


# ────────────────────────────────────────────────────────────
# 3. seed_foreshadowing_from_setting 幂等去重
# ────────────────────────────────────────────────────────────

def test_seed_foreshadowing_dedup():
    """DB 里的伏笔种子应该幂等灌入 L2：重复调用不产生重复条目。"""
    from engine.agents import tracker  # noqa
    # 直接调 memory manager 的新 helper
    from engine.memory.manager import (
        seed_foreshadowing_from_setting,
        get_l2,
    )

    setting = {
        "foreshadowing_seeds": [
            {"content": "陈昭不喝酒的原因", "target_arc": 4},
            {"content": "苏晚萤保存的尸检副本", "target_arc": 3},
        ],
    }
    with patch("engine.memory.manager.get_l2", return_value={
        "hot": {}, "cold": {},
        "constraints": {"foreshadowing_planted": []},
        "meta": {},
    }), patch("engine.memory.manager.save_l2"):
        n1 = seed_foreshadowing_from_setting("x", setting)
        n2 = seed_foreshadowing_from_setting("x", setting)
        assert n1 == 2
        assert n2 == 0, "重复调用应返回 0（全部已存在）"


def test_seed_foreshadowing_keeps_existing_planted():
    """已存在的伏笔（含 tracker 动态写入的）不能被种子去重覆盖。"""
    from engine.memory.manager import seed_foreshadowing_from_setting

    mem = {
        "hot": {}, "cold": {},
        "constraints": {"foreshadowing_planted": [
            {"desc": "动态埋设的伏笔", "planted_at_chapter": 10, "source": "tracker"}
        ]},
        "meta": {},
    }
    setting = {"foreshadowing_seeds": [
        {"content": "动态埋设的伏笔", "target_arc": 2},  # 内容相同
    ]}
    with patch("engine.memory.manager.get_l2", return_value=mem), \
         patch("engine.memory.manager.save_l2"):
        n = seed_foreshadowing_from_setting("x", setting)
        # 因 desc 已存在（动态埋设条目），种子被跳过
        assert n == 0


# ────────────────────────────────────────────────────────────
# 4. 量纲修复：foreshadowing_due_soon 按章号触发
# ────────────────────────────────────────────────────────────

def test_foreshadow_due_soon_uses_chapter_number():
    """回归：量纲错误修复后，target_arc*30 估算的章号能正确触发 due_soon。"""
    from engine.memory.manager import (
        get_chapter_relevant_context, _foreshadow_target_chapter,
    )

    # 弧 4 → target_chapter ≈ 120
    assert _foreshadow_target_chapter({"target_arc": 4}) == 120
    # 显式 target_chapter 优先
    assert _foreshadow_target_chapter({"target_arc": 4, "target_chapter": 50}) == 50
    # 都没填 → 用 planted_at + 30
    assert _foreshadow_target_chapter({"planted_at_chapter": 10}) == 40
    # 全空 → 返回超大数（永不触发）
    assert _foreshadow_target_chapter({}) >= 10**8

    # 集成：当前 ch_num=100，target_chapter=120（弧4）应在 due_soon 里
    memory = {
        "hot": {}, "cold": {"resolved_foreshadowing": []},
        "constraints": {"foreshadowing_planted": [
            {"desc": "弧4伏笔", "target_arc": 4, "planted_at_chapter": 60},
        ]},
        "meta": {},
    }
    task = {"chapter_number": 100, "main_characters": []}
    ctx = get_chapter_relevant_context(memory, task)
    assert "弧4伏笔" in ctx["foreshadowing_due_soon"], (
        f"应在 due_soon 里，实际={ctx['foreshadowing_due_soon']}"
    )