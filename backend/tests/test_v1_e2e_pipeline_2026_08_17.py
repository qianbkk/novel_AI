"""test_v1_e2e_pipeline_2026_08_17.py

v1.0 Stage J 验证：完整 pre-production pipeline 串联（mock LLM），
模拟用户从新建项目 → 题材画像 → 共性主题 → 黄金三章 → 资料助手
→ 写每章（带 v1.0 writer prompt）→ memory ledgers 的完整链路。

真实 30 章 LLM 跑（docs/wiki/07-Real-LLM-Testing.md）需要 MiniMax API key，
本测试是 mock 版本，验证模块串联 + 数据流转。

CLAUDE.md 红线：
- 用户编辑 PUT 强制 source='user'，不能被覆盖
- 缺字段 → 400
- 链路上一个步骤的输出可被下一个步骤消费
"""

from __future__ import annotations

import pytest


# ════════════════════════════════════════════════════════════════
# Pre-Production 完整链路
# ════════════════════════════════════════════════════════════════

def test_pre_production_full_pipeline(tmp_path, monkeypatch):
    """完整 pre-production 流程串联：题材 → 主题 → 开篇 → 资料 → 宏观弧。
    验证每步的输出可被下一步消费（schema 一致性）。"""
    from engine.config import paths as paths_mod
    import os
    os.environ["NOVEL_AI_DIR"] = str(tmp_path)

    from engine.agents.genre_profiler import profile_genre
    from engine.agents.theme_designer import design_theme
    from engine.agents.opening_designer import design_opening
    from engine.agents.research_notes import init_research_notes, query_notes
    from engine.agents.macro_spine import design_macro_spine
    from engine.agents.macro_spine import get_arc_for_chapter

    # 1. 题材画像
    profile = profile_genre("lishi", use_llm=False, novel_id="test")
    assert profile["research_strength"] == "strong"

    # 2. 共性主题（依赖 profile）
    theme = design_theme(
        concept="归家主题", genre_profile=profile, key_characters=[],
        use_llm=False, novel_id="test",
    )
    assert theme["theme_statement"]
    assert any("家" in a for a in theme["resonance_anchors"])

    # 3. 黄金三章（依赖 profile + theme）
    opening = design_opening(
        concept="", theme_spine=theme, genre_profile=profile,
        key_characters=[], use_llm=False, novel_id="test",
    )
    assert opening["chapter_1_anchor"]["hook_type"]
    assert opening["chapter_3_escalation"]["hook_type"]

    # 4. 资料助手（依赖 profile.research_strength）
    notes = init_research_notes(
        genre_profile=profile, concept="", use_llm=False, novel_id="test",
    )
    assert notes["research_strength"] == "strong"
    # 5 维度 baseline
    for d in ("朝代", "地理", "职官", "物价", "服饰"):
        assert d in notes["baseline"]
    # query API
    notes_text = query_notes("test", chapter=1)
    assert "朝代" in notes_text or "米" in notes_text

    # 5. 宏观弧（依赖 theme）
    spine = design_macro_spine(
        theme_spine=theme, opening_design=opening,
        total_chapters=80, use_llm=False, novel_id="test",
    )
    assert len(spine["arcs"]) >= 2
    # arc 边界连续
    arcs = spine["arcs"]
    for i in range(len(arcs) - 1):
        assert arcs[i]["end_chapter"] + 1 == arcs[i + 1]["start_chapter"]

    # 6. get_arc_for_chapter — 写每章前能查所属 arc
    arc_5 = get_arc_for_chapter(spine, 5)
    assert arc_5 is not None
    assert arc_5["start_chapter"] <= 5 <= arc_5["end_chapter"]


# ════════════════════════════════════════════════════════════════
# Writer Prompt v2 + Memory Ledgers 串联
# ════════════════════════════════════════════════════════════════

def test_writer_prompt_v2_consumes_pre_production_outputs():
    """writer prompt v2 必须能把 pre-production 全部产物注入。"""
    from engine.agents.writer import build_writer_prompt, _build_genre_block, _build_theme_block
    from engine.agents.writer import _build_expectation_block, _build_showitem_block

    # 模拟完整 pre-production 数据
    setting = {
        "genre": "历史",
        "protagonist": {"name": "主角"},
        "v1_genre_profile": {
            "genre": "历史", "tone_preference": "沉郁克制",
            "reader_persona": {"primary": "30-50 男性"},
            "show_item_examples": ["那双布鞋"],
            "taboo": ["后宫"],
        },
        "v1_theme_spine": {
            "theme_statement": "在大时代里，普通人能守住的只有'回家'",
            "resonance_anchors": ["家", "忠诚"],
            "expectation_arc": {"description": "归家弧"},
        },
        "v1_macro_spine": {
            "arcs": [{"arc_id": 1, "name": "开局", "start_chapter": 1,
                       "end_chapter": 15, "theme_focus": "归途期待",
                       "expectation_progress": "seed 强化",
                       "main_conflict": "服徭役 vs 回家", "tone": "克制"}],
        },
    }
    context = {
        "v1_expectation_ledger": [
            {"chapter_number": 1, "show_item_used": ["那双布鞋"]},
            {"chapter_number": 2, "show_item_used": ["那双布鞋", "邻家少年的眼睛"]},
        ],
    }
    task = {
        "chapter_number": 3, "chapter_goal": "主角被征召",
        "main_characters": ["主角"], "ending_hook_type": "悬念钩",
        "emotion_core": "期待", "emotion_intensity": 3,
    }

    system, user = build_writer_prompt(task, context, setting)

    # 4 个 v1.0 block 都应在 user prompt 里
    assert "沉郁克制" in user, "genre block"
    assert "回家" in user, "theme block"
    assert "开局" in user, "expectation block"
    assert "那双布鞋" in user, "show-item block"


# ════════════════════════════════════════════════════════════════
# Scene Quality Check 串联
# ════════════════════════════════════════════════════════════════

def test_scene_quality_check_in_pipeline_with_no_llm():
    """scene_quality_check 纯数据模式（CI 友好）能跑完整 chapter + card。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    chapter_text = (
        "主角看鞋一眼，想起母亲做这双鞋时的针脚。"
        "家的方向突然变得陌生。"
    )
    card = {
        "chapter_number": 3,
        "expectation_progress": {
            "seed_1_status": "期待回家",
            "seed_1_change": "家的方向变成谜团",
        },
        "show_item_required": ["那双布鞋"],
        "resonance_anchor_target": "家不只是一个地址",
    }
    result = run_scene_quality_check(
        chapter_text=chapter_text,
        chapter_card=card,
        lorebook_hits=[],
        use_llm=False,
    )
    # 4 维度数据检测都过（expectation 推进 + show-item 命中）
    assert result["expectation_advanced"] is True
    assert result["show_item_landed"] is True
    assert result["reasons"] == []  # 无失败原因


def test_scene_quality_check_escalates_on_missing_show_item():
    """show-item 没落地 → should_escalate=True，reasons 包含具体指引。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    result = run_scene_quality_check(
        chapter_text="主角决定离开，去远方。",  # 没有"鞋"
        chapter_card={
            "chapter_number": 3,
            "expectation_progress": {
                "seed_1_status": "扭曲", "seed_1_change": "家的方向变成谜团",
            },
            "show_item_required": ["那双布鞋"],
            "resonance_anchor_target": "家",
        },
        lorebook_hits=[],
        use_llm=False,
    )
    assert result["should_escalate"] is True
    assert any("show-item" in r for r in result["reasons"])


# ════════════════════════════════════════════════════════════════
# Memory Ledgers 串联
# ════════════════════════════════════════════════════════════════

def test_memory_ledgers_full_chapter_lifecycle(tmp_path, monkeypatch):
    """模拟一整章完整生命周期：
    写章节 → append expectation → append show-item → record voice → 下章读取。"""
    import os
    os.environ["NOVEL_AI_DIR"] = str(tmp_path)

    from engine.memory.expectation_ledger import (
        append_expectation, load_ledger, get_pending_seeds
    )
    from engine.memory.show_item_chain import (
        append_show_item, get_recent_items
    )
    from engine.memory.voice_anchors import (
        record_voice, check_voice_consistency
    )

    # === ch1 写完 ===
    append_expectation("proj", 1, {
        "seed_1_status": "首次播种",
        "new_seed": "主角想回家",
    })
    append_show_item("proj", 1, ["那双布鞋"])
    record_voice("proj", "主角", ["这局我来开局"])

    # === ch2 写完 ===
    append_expectation("proj", 2, {
        "seed_1_status": "种子强化",
        "seed_1_change": "家的方向变成谜团",
    })
    append_show_item("proj", 2, ["那双布鞋", "邻家少年的眼睛"])

    # === ch3 写之前：writer prompt 读 ledgers ===
    pending = get_pending_seeds("proj", exclude_chapter=3)
    # 之前 ch1/ch2 都在追踪 seed_1（status + change 两个 key）
    assert any(k.startswith("seed_1") for k in pending), (
        f"应至少有 seed_1_* 类 key，实际 {pending}"
    )

    recent_items = get_recent_items("proj", last_n=3)
    # 最近 2 章的 items 都应能拿到
    flat = [item for items in recent_items.values() for item in items]
    assert "那双布鞋" in flat
    assert "邻家少年的眼睛" in flat

    # voice consistency: ch3 正文是否用了口癖
    ch3_text = "主角说：这局我来开局。"
    voice_result = check_voice_consistency("proj", "主角", ch3_text)
    assert voice_result["used_anchors"] == ["这局我来开局"]


# ════════════════════════════════════════════════════════════════
# API 全链路
# ════════════════════════════════════════════════════════════════

def test_api_endpoints_full_pre_production_flow():
    """端到端 API 流程：genre → theme → opening → research，全部走 HTTP。
    用 module-level DB（与 test_pre_production_api 同模式）保证可独立运行。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, SessionLocal
    from app.models import Project

    client = TestClient(app)
    pid = "e2e-pipeline-test"
    db = SessionLocal()
    try:
        proj = db.get(Project, pid)
        if proj is None:
            proj = Project(id=pid, title="e2e", genre="历史", config_json={})
            db.add(proj)
            db.commit()
    finally:
        db.close()

    # 1. 题材画像
    r = client.post(
        f"/projects/{pid}/pre-production/genre-profile/generate",
        json={"genre_key": "lishi", "use_llm": False},
    )
    assert r.status_code == 200

    # 2. 共性主题
    r = client.post(
        f"/projects/{pid}/pre-production/theme/generate",
        json={"concept": "归家", "use_llm": False},
    )
    assert r.status_code == 200

    # 3. 黄金三章
    r = client.post(
        f"/projects/{pid}/pre-production/opening/generate",
        json={"concept": "", "use_llm": False},
    )
    assert r.status_code == 200

    # 4. 资料助手
    r = client.post(
        f"/projects/{pid}/pre-production/research-notes/initialize",
        json={"concept": "", "use_llm": False},
    )
    assert r.status_code == 200
    notes = r.json()
    assert notes["research_strength"] == "strong"

    # 5. 用户编辑 PUT 强制 source='user'
    theme_payload = {
        "theme_statement": "我手工改的主题",
        "expectation_arc": {
            "seed_chapter": 1, "payoff_chapter": 80, "twist_chapter": 25,
            "description": "user edited",
        },
        "resonance_anchors": ["家", "忠诚", "孤独"],
        "source": "user",
    }
    r = client.put(f"/projects/{pid}/pre-production/theme", json=theme_payload)
    assert r.status_code == 200

    # 6. 读出来是用户编辑版
    r = client.get(f"/projects/{pid}/pre-production/theme")
    assert r.json()["source"] == "user"
    assert r.json()["theme_statement"] == "我手工改的主题"

    # cleanup
    db = SessionLocal()
    try:
        db.query(Project).filter_by(id=pid).delete()
        db.commit()
    finally:
        db.close()