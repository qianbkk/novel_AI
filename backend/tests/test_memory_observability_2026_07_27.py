"""test_memory_observability_2026_07_27.py

架构审视 — 分层记忆完全不可观测。

背景（本轮实测发现）：
- 后端**没有任何**暴露 L2/L5 的 endpoint（`app/api/` 全域 grep 只有 outline.py
  内部调 `get_l2`）。
- 前端号称有"记忆层"展示，但 `Dashboard.memoryDepth()` 是
  `l1 = status==="ready" ? 5 : 1`、`l2 = min(12, chapters.length)`、
  `l3 = log10(words)*3`；`BridgeConsole` 三个温度计同理由章节数/字数硬算。
  **没有一个字节来自真实记忆文件**，而且标注的 L1/L3 层在本项目里根本不存在
  （只有 L2 热冷约束 + L5 弧归档）。

后果：长篇写作里记忆漂移是头号杀手，而记忆状态既无 API 也无界面 —— 只能去
绑定目录手翻 JSON。前几轮补的伏笔调度、质量债标记、弧长基准全都不可验收。

本次补齐 `read_memory()`：与既有 `read_status/read_pending/read_budget_log`
同构（读绑定目录、env 优先、缺文件不抛），把真实 L2/L5 摘要暴露出来，并把
前几轮埋的信号（unverified 标记 / 伏笔逾期计数 / chapters_per_arc）一并上浮。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import pytest  # noqa: E402

from app.bridge.reports import read_memory  # noqa: E402
from _test_db import isolated_test_db  # noqa: E402,F401  -- api_client 依赖


NOVEL = "nid"


@pytest.fixture
def bind(tmp_path, monkeypatch):
    """造一个绑定目录，并让 read_memory 走 NOVEL_AI_DIR env（与 engine 一致）。"""
    monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
    return tmp_path


def _write(bind: Path, layer: str, payload: dict, novel_id: str = NOVEL) -> None:
    d = bind / "memory" / layer
    d.mkdir(parents=True, exist_ok=True)
    suffix = "memory" if layer == "l2" else "l5"
    (d / f"{novel_id}_{suffix}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _l2(**over) -> dict:
    base = {
        "hot": {
            "protagonist_level": "法师", "protagonist_level_num": 2,
            "protagonist_points": 30, "inventory": ["魔石"],
            "active_threads": ["夺回徽记", "回廊封锁"],
            "character_states": {"莉拉": "受伤", "凯恩": "在位"},
            "recent_summaries": [
                {"chapter": 5, "summary": "夺回徽记"},
                {"chapter": 6, "summary": "对峙失败", "unverified": True},
            ],
            "recent_events": "夺回徽记 | 对峙失败（第6章待修订，情节未定稿）",
            "scene_location": "深渊回廊", "time_context": "入夜",
            "last_chapter_ending": "魔石耗尽",
        },
        "cold": {
            "world_events": ["王国财政崩溃"],
            "closed_threads": ["旧债"],
            "resolved_foreshadowing": ["血脉伏笔"],
        },
        "constraints": {
            "forbidden_constraints": [
                {"id": "c1", "desc": "不得复活", "expires_at_chapter": 30}],
            "established_facts": [{"fact": "回廊封闭千年",
                                   "established_at_chapter": 3}],
            "foreshadowing_planted": [
                {"desc": "早该回收", "planted_at_chapter": 1, "target_chapter": 4},
                {"desc": "本章到期", "planted_at_chapter": 2, "target_chapter": 6},
                {"desc": "还早", "planted_at_chapter": 5, "target_chapter": 40},
            ],
        },
        "meta": {"last_updated_chapter": 6, "total_chapters_tracked": 6,
                 "chapters_per_arc": 25},
    }
    base.update(over)
    return base


# ─── 1. 缺文件 / 坏文件：不抛，明确降级 ─────────────────────────

def test_missing_memory_reports_unavailable(bind):
    out = read_memory(str(bind), NOVEL)
    assert out["available"] is False
    assert out["l2_available"] is False and out["l5_available"] is False
    assert out["message"]


def test_missing_novel_id_reports_unavailable(bind):
    """binding.novel_id 没配时不能猜文件名。"""
    out = read_memory(str(bind), None)
    assert out["available"] is False
    assert "novel_id" in out["message"]


def test_corrupted_l2_is_reported_not_swallowed(bind):
    d = bind / "memory" / "l2"
    d.mkdir(parents=True)
    (d / f"{NOVEL}_memory.json").write_text("{ 不是 JSON", encoding="utf-8")
    out = read_memory(str(bind), NOVEL)
    # 失败要响亮：不能假装"没有记忆"
    assert out["l2_available"] is False
    assert "corrupt" in (out.get("message") or "").lower() or out.get("l2_error")


def test_l5_missing_does_not_hide_l2(bind):
    _write(bind, "l2", _l2())
    out = read_memory(str(bind), NOVEL)
    assert out["available"] is True
    assert out["l2_available"] is True
    assert out["l5_available"] is False


# ─── 2. 真实 L2 内容上浮 ─────────────────────────

def test_hot_layer_is_exposed(bind):
    _write(bind, "l2", _l2())
    hot = read_memory(str(bind), NOVEL)["l2"]["hot"]
    assert hot["protagonist_level"] == "法师"
    assert hot["scene_location"] == "深渊回廊"
    assert hot["active_threads"] == ["夺回徽记", "回廊封锁"]
    assert hot["character_states"]["莉拉"] == "受伤"


def test_cold_and_constraints_layers_are_exposed(bind):
    _write(bind, "l2", _l2())
    m = read_memory(str(bind), NOVEL)["l2"]
    assert m["cold"]["world_events"] == ["王国财政崩溃"]
    assert m["constraints"]["forbidden_constraints"][0]["desc"] == "不得复活"
    assert m["constraints"]["established_facts"][0]["fact"] == "回廊封闭千年"


def test_meta_is_exposed(bind):
    _write(bind, "l2", _l2())
    meta = read_memory(str(bind), NOVEL)["l2"]["meta"]
    assert meta["last_updated_chapter"] == 6
    assert meta["chapters_per_arc"] == 25


# ─── 3. 前几轮埋的信号必须可见（否则那些修复无法验收）─────────────────────────

def test_unverified_chapter_is_visible(bind):
    """质量债隔离（3f3d4f9）：没过质量门的章节前端要能看出来。"""
    _write(bind, "l2", _l2())
    stats = read_memory(str(bind), NOVEL)["stats"]
    assert stats["unverified_chapter_count"] == 1
    assert 6 in stats["unverified_chapters"]


def test_no_unverified_reports_zero(bind):
    mem = _l2()
    for s in mem["hot"]["recent_summaries"]:
        s.pop("unverified", None)
    _write(bind, "l2", mem)
    stats = read_memory(str(bind), NOVEL)["stats"]
    assert stats["unverified_chapter_count"] == 0
    assert stats["unverified_chapters"] == []


def test_foreshadow_overdue_is_visible(bind):
    """伏笔调度（c649d1e）：逾期堆积是长篇最致命的静默故障，必须可观测。

    last_updated_chapter=6：target 4 已超期，target 6 本章到期（未超期），
    target 40 还早。
    """
    _write(bind, "l2", _l2())
    stats = read_memory(str(bind), NOVEL)["stats"]
    assert stats["foreshadowing_planted_count"] == 3
    assert stats["foreshadowing_resolved_count"] == 1
    assert stats["foreshadowing_overdue_count"] == 1


def test_overdue_uses_arc_length_when_only_target_arc_given(bind):
    """只给 target_arc 时按 meta.chapters_per_arc 折算，与 manager 口径一致。"""
    mem = _l2()
    mem["meta"]["chapters_per_arc"] = 3
    mem["meta"]["last_updated_chapter"] = 10
    mem["constraints"]["foreshadowing_planted"] = [
        {"desc": "弧1回收", "planted_at_chapter": 1, "target_arc": 1},   # → 第3章，逾期
        {"desc": "弧9回收", "planted_at_chapter": 1, "target_arc": 9},   # → 第27章，未到
    ]
    _write(bind, "l2", mem)
    stats = read_memory(str(bind), NOVEL)["stats"]
    assert stats["foreshadowing_overdue_count"] == 1


def test_tracker_parse_failures_surface(bind):
    """tracker 解析失败会静默吞掉一整章的状态提取，必须上浮。"""
    mem = _l2()
    mem["meta"]["tracker_parse_failure_count"] = 2
    mem["meta"]["last_tracker_parse_failure_chapter"] = 5
    _write(bind, "l2", mem)
    stats = read_memory(str(bind), NOVEL)["stats"]
    assert stats["tracker_parse_failure_count"] == 2


# ─── 4. L5 弧归档 ─────────────────────────

def test_l5_arc_summaries_exposed(bind):
    _write(bind, "l2", _l2())
    _write(bind, "l5", {
        "arc_summaries": [{"arc": 1, "summary": "第一弧：夺回家族徽记"}],
        "character_arcs": {"艾德里安": "从落魄到法师"},
        "major_revelations": ["回廊由龙裔守护"],
        "compressed_history": "前六章压缩史",
    })
    out = read_memory(str(bind), NOVEL)
    assert out["l5_available"] is True
    assert out["l5"]["arc_summaries"][0]["arc"] == 1
    assert out["l5"]["character_arcs"]["艾德里安"]
    assert out["stats"]["arc_count"] == 1


# ─── 5. 体积受控：不能把整份记忆无脑吐出来 ─────────────────────────

def test_long_lists_are_capped_with_total_reported(bind):
    mem = _l2()
    mem["hot"]["recent_summaries"] = [
        {"chapter": i, "summary": f"第{i}章"} for i in range(1, 101)]
    mem["cold"]["world_events"] = [f"事件{i}" for i in range(200)]
    _write(bind, "l2", mem)
    out = read_memory(str(bind), NOVEL)
    assert len(out["l2"]["hot"]["recent_summaries"]) <= 20
    assert len(out["l2"]["cold"]["world_events"]) <= 50
    # 截断必须留痕，不能让前端误以为"就这么多"
    assert out["stats"]["recent_summaries_total"] == 100
    assert out["stats"]["world_events_total"] == 200


def test_capped_list_keeps_the_most_recent(bind):
    mem = _l2()
    mem["hot"]["recent_summaries"] = [
        {"chapter": i, "summary": f"第{i}章"} for i in range(1, 101)]
    _write(bind, "l2", mem)
    kept = read_memory(str(bind), NOVEL)["l2"]["hot"]["recent_summaries"]
    assert kept[-1]["chapter"] == 100


def test_unverified_count_counts_all_not_just_the_visible_tail(bind):
    """截断不能让统计失真 —— 计数要基于全量。"""
    mem = _l2()
    mem["hot"]["recent_summaries"] = (
        [{"chapter": i, "summary": "x", "unverified": True} for i in range(1, 31)]
        + [{"chapter": i, "summary": "y"} for i in range(31, 101)])
    _write(bind, "l2", mem)
    assert read_memory(str(bind), NOVEL)["stats"]["unverified_chapter_count"] == 30


# ─── 6. 畸形数据不能打穿 ─────────────────────────

@pytest.mark.parametrize("payload", [
    [], "字符串", 42, None,
    {"hot": "不是 dict"},
    {"hot": {"recent_summaries": "不是 list"}},
    {"constraints": {"foreshadowing_planted": [None, "串", 7]}},
    {"hot": {"recent_summaries": [None, "串", {"summary": "ok"}]}},
])
def test_malformed_memory_does_not_crash(bind, payload):
    _write(bind, "l2", payload)
    out = read_memory(str(bind), NOVEL)
    assert isinstance(out, dict)
    assert isinstance(out.get("stats"), dict)


# ─── 7. 路径解析：与 read_status 同一规则 ─────────────────────────

def test_env_dir_wins_over_argument(bind, tmp_path_factory):
    """NOVEL_AI_DIR 已设时忽略传入的目录（bridge 注入 env 后两边必须一致）。"""
    _write(bind, "l2", _l2())
    other = tmp_path_factory.mktemp("other")
    assert read_memory(str(other), NOVEL)["l2_available"] is True


def test_falls_back_to_argument_when_env_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
    _write(tmp_path, "l2", _l2())
    assert read_memory(str(tmp_path), NOVEL)["l2_available"] is True


def test_no_secrets_in_payload(bind):
    """记忆里不该、也不得夹带任何凭据字段。"""
    mem = _l2()
    mem["meta"]["api_key"] = "sk-should-never-surface"
    _write(bind, "l2", mem)
    blob = json.dumps(read_memory(str(bind), NOVEL), ensure_ascii=False)
    assert "sk-should-never-surface" not in blob


# ─── 8. endpoint 接线 ─────────────────────────

def test_endpoint_is_registered():
    from app.api.bridge import router
    paths = {r.path for r in router.routes}
    assert "/projects/{project_id}/bridge/memory" in paths, \
        f"bridge router 没有 /memory：{sorted(paths)}"


def test_endpoint_requires_binding(api_client, monkeypatch):
    """没绑定的项目应当是 4xx，而不是 500 或空 200。"""
    r = api_client.get("/projects/does-not-exist/bridge/memory")
    assert r.status_code in (401, 403, 404), r.status_code
