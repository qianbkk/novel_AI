"""tools/system_test.py — 全系统集成测试 (P2/P3 移植版)

Migrated from novel_AI/tools/system_test.py. Tests cover:
  - Module imports (agents / memory / config / tools)
  - Memory L2/L5 roundtrip
  - Checker weighted score math
  - Normalizer AI-word replacement
  - Compliance keyword scan
  - Fingerprint analyzer (stats-only)
  - LangGraph graph compile + SqliteSaver tables
  - Stub pipeline (mock LLM calls)

Mocks LLM via patching router.call to avoid real API costs.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Ensure backend/ is on the path when run as a script
_BACKEND = Path(__file__).resolve().parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


P = "✅"
F = "❌"
results: list = []


def test(name: str):
    def dec(fn):
        def wrap():
            try:
                fn()
                results.append((P, name, ""))
                print(f"  {P} {name}")
            except Exception as e:
                results.append((F, name, str(e)))
                print(f"  {F} {name}: {e}")
        return wrap
    return dec


MOCK_CHAPTER = """陆承把笔放在桌上，看着对面那个人的西装口袋。
三十分钟了。对方一句有效信息都没给出来。
"说吧。"陆承开口。
对面的男人愣了一下，像是没想到他会这么直接。然后，陆承看到了那条线。
红色的，粗得异常，从那个男人的左胸延伸出去，穿过玻璃幕墙，消失在城市某处。
陆承眨了眨眼。线还在。
【人情感知已激活】【检测到债崩预警：红色债链临界值98%】
"林总，"陆承重新拿起笔，"你现在需要做的不是跟我谈条款。"
林成远突然捂住胸口。救护车是陆承叫的。
手机屏幕还亮着——那条系统通知他没有滑掉。「欢迎回来，陆氏第四代传人。」"""

MOCK_CHECKER_RESP = json.dumps({
    "dimensions": {"hook_power": 8, "shuang_density": 7, "character_voice": 8,
                   "plot_logic": 8, "writing_naturalness": 8},
    "overall_score": 7.8, "strongest_point": "节奏感强",
    "weakest_point": "爽感可更密集", "specific_feedback": "整体良好",
})
MOCK_COMPLIANCE_RESP = json.dumps({"passed": True, "hard_rejects": [], "warnings": [], "suggestion": ""})
MOCK_TRACKER_RESP = json.dumps({
    "protagonist_points": 150, "character_states": {"陆承": "觉醒状态"},
    "active_threads": ["人情债觉醒"], "last_chapter_ending": "系统通知未滑掉",
    "chapter_summary": "陆承觉醒人情感知",
})
MOCK_OUTLINE_RESP = json.dumps([
    {"chapter_number": 1, "chapter_role": "开局", "chapter_goal": "觉醒",
     "main_characters": ["陆承"], "shuang_type": "揭秘", "shuang_description": "看到债线",
     "ending_hook_type": "信息钩", "ending_hook_description": "系统通知",
     "setting_constraints": [], "forbidden_actions": [], "target_length": "2000-2200",
     "audit_mode": "lite", "is_arc_climax": False},
] * 5)
MOCK_SUMMARY_RESP = json.dumps({
    "arc_id": 1, "arc_name": "开局", "summary_100": "陆承觉醒",
    "key_events": [], "protagonist_growth": "",
    "relationships_changed": [], "unresolved_threads": [],
    "foreshadowing_planted": [], "ending_state": "觉醒完成",
})


def _mock_call_router(agent_name: str, *args, **kwargs):
    if agent_name in ("checker_main", "checker_cross1", "checker_cross2"):
        return MOCK_CHECKER_RESP, 0.001
    if agent_name == "compliance":
        return MOCK_COMPLIANCE_RESP, 0.001
    if agent_name == "tracker":
        return MOCK_TRACKER_RESP, 0.001
    if agent_name == "writer":
        return MOCK_CHAPTER, 0.005
    if agent_name == "outline":
        return MOCK_OUTLINE_RESP, 0.01
    if agent_name == "summarizer":
        return MOCK_SUMMARY_RESP, 0.01
    if agent_name == "rewriter":
        return MOCK_CHAPTER, 0.005
    if agent_name == "normalizer":
        # After normalizer first-pass replaces, send back the input as-is
        return kwargs.get("user_prompt", MOCK_CHAPTER), 0.001
    return json.dumps({"result": "mock"}), 0.001


# ═══════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════
@test("导入：engine state")
def t1():
    from engine.state import create_initial_state, save_state, load_state
    s = create_initial_state("t", "测试", "fanqie", "都市", "设定", 100.)
    assert s["novel_id"] == "t" and s["budget_limit_usd"] == 100.


@test("导入：LLM router + LLMRouter")
def t2():
    from engine.llm.router import LLMRouter, MODEL_ROUTES_DEFAULT
    from engine.llm_router import get_active_router, reset_active_router
    assert "writer" in MODEL_ROUTES_DEFAULT and len(MODEL_ROUTES_DEFAULT) >= 10
    router = LLMRouter()
    router.reset_stats()
    assert router.get_stats()["total_calls"] == 0
    reset_active_router()


@test("导入：所有 Agent")
def t3():
    from engine.agents import (
        run_writer, run_normalizer, run_compliance, run_checker,
        run_rewriter, run_tracker, run_summarizer, run_outline,
    )


@test("导入：所有工具")
def t4():
    from engine.tools import (
        bootstrap, budget_manager, chapter_checker, fingerprint_checker,
        exporter, human_review, style_manager, calibrate_checker,
        acceptance_tests,
    )


@test("LangGraph 图编译")
def t5():
    from engine.orchestrator import build_graph
    g = build_graph()
    assert g is not None


@test("prompt_templates：题材切换")
def t7():
    from engine.config import HOOK_TYPES, SHUANG_TYPES, get_genre_instruction, get_hook_guidance
    assert len(HOOK_TYPES) == 7
    assert len(SHUANG_TYPES) >= 5
    urban = get_genre_instruction("都市系统流")
    assert "系统流" in urban
    hook = get_hook_guidance("反转钩")
    assert "反转钩" in hook


@test("memory_manager V2：热冷分离")
def t8():
    from engine.memory.manager import empty_l2, expire_constraints, add_constraint, maybe_compress_hot_to_cold
    m = empty_l2()
    m = add_constraint(m, "不能透露身份", 10, "测试")
    assert len(m["constraints"]["forbidden_constraints"]) == 1
    m2, expired = expire_constraints(m, 11)
    assert expired == 1
    assert len(m2["constraints"]["forbidden_constraints"]) == 0
    for i in range(25):
        m["hot"]["recent_summaries"].append({"chapter": i+1, "summary": f"第{i+1}章"})
    m = maybe_compress_hot_to_cold(m, "test")
    assert len(m["hot"]["recent_summaries"]) == 15


@test("memory_manager V2：按需检索")
def t9():
    from engine.memory.manager import empty_l2, get_chapter_relevant_context
    m = empty_l2()
    m["hot"]["protagonist_level"] = "识债者"
    m["hot"]["protagonist_points"] = 600
    m["hot"]["character_states"] = {"陆承": "正常", "贺苗": "神秘", "章廷": "监视中"}
    task = {"chapter_number": 5, "main_characters": ["陆承", "贺苗"]}
    ctx = get_chapter_relevant_context(m, task)
    assert "陆承" in ctx["character_states"]
    assert "贺苗" in ctx["character_states"]
    assert ctx["protagonist_level"] == "识债者"


@test("Normalizer：词汇替换+AI检测")
def t10():
    from engine.agents.normalizer import first_pass_replace
    text = "此刻他蓦然不禁心中一动，深吸一口气，眼眸中闪着光"
    result, count = first_pass_replace(text)
    assert count > 3
    assert "此刻" not in result or "蓦然" not in result


@test("Compliance：合规检测")
def t11():
    from engine.agents.compliance import keyword_scan
    hr, _ = keyword_scan("陆承走进写字楼，看到那条红色的债线。")
    assert hr == []
    hr2, _ = keyword_scan("习近平下令执行了这个计划")
    assert len(hr2) > 0


@test("Checker：五维加权评分")
def t12():
    from engine.agents.checker import calculate_weighted_score
    dims = {"hook_power": 9, "shuang_density": 8, "character_voice": 7,
            "plot_logic": 8, "writing_naturalness": 7}
    score = calculate_weighted_score(dims)
    assert 7.0 <= score <= 9.5


@test("fingerprint_checker：统计检测")
def t13():
    from engine.tools.fingerprint_checker import analyze_fingerprint
    normal = """陆承把合同推回去。
"条款三。"他说。
对面的男人笑了起来，笑声很假。
陆承站起来，走出去。"""
    r = analyze_fingerprint(normal)
    assert r["ai_score"] < 60
    ai = """此刻陆承不禁心中一动，深吸一口气，眼眸中闪烁着莫名的光芒。蓦然，他感到心中涌上一丝不由得的感慨。"""
    r2 = analyze_fingerprint(ai)
    assert r2["ai_score"] > r["ai_score"]


@test("SqliteSaver 真实接入")
def t14():
    from engine.graph import _get_or_open_checkpointer, close_all_checkpointers
    tmp_path = os.path.join(tempfile.gettempdir(), "test_smoke_checkpoints.sqlite")
    try:
        Path(tmp_path).unlink()
    except FileNotFoundError:
        pass
    saver = _get_or_open_checkpointer(tmp_path)
    assert saver is not None
    conn = sqlite3.connect(tmp_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    assert "checkpoints" in tables and "writes" in tables
    conn.close()
    # Reuse
    saver2 = _get_or_open_checkpointer(tmp_path)
    assert saver is saver2
    close_all_checkpointers()
    try:
        Path(tmp_path).unlink()
    except FileNotFoundError:
        pass


@test("完整写作流水线（Mock）")
def t15():
    from unittest.mock import patch
    with patch("engine.llm.router.LLMRouter.call", side_effect=_mock_call_router), \
         patch("engine.llm_router.get_active_router") as mock_get:
        from engine.llm.router import LLMRouter
        mock_get.return_value = LLMRouter()
        from engine.agents.normalizer import run_normalizer
        from engine.agents.compliance import run_compliance
        from engine.agents.checker import run_checker
        task = {"chapter_number": 1, "chapter_role": "开局", "chapter_goal": "觉醒",
                "main_characters": ["陆承"], "shuang_type": "揭秘",
                "shuang_description": "看到债线",
                "ending_hook_type": "信息钩", "ending_hook_description": "系统通知",
                "setting_constraints": [], "forbidden_actions": [],
                "target_length": "2000-2200", "audit_mode": "lite",
                "is_arc_climax": False}
        clean, issues, c1 = run_normalizer(MOCK_CHAPTER, task)
        assert len(clean) > 100 and c1 >= 0
        comp, c2 = run_compliance(clean)
        assert comp["passed"] is True
        check, c3 = run_checker(clean, task, "lite")
        assert check["score"] > 0
        assert check["verdict"] in ("PASS", "PASS_WITH_NOTE",
                                    "REWRITE_LIGHT", "REWRITE_MEDIUM", "REWRITE_HEAVY")


@test("Tracker V2：新L2 Schema更新")
def t16():
    from unittest.mock import patch
    with patch("engine.llm.router.LLMRouter.call", side_effect=_mock_call_router):
        from engine.agents.tracker import run_tracker
        from engine.memory.manager import empty_l2
        mem = empty_l2()
        mem["meta"]["novel_id"] = "test_tracker_smoke"
        task = {"chapter_number": 1, "chapter_role": "开局", "chapter_goal": "测试",
                "main_characters": ["陆承"], "shuang_description": "",
                "ending_hook_description": "", "target_length": "2000",
                "audit_mode": "lite", "is_arc_climax": False,
                "setting_constraints": [], "forbidden_actions": []}
        updated, cost = run_tracker(MOCK_CHAPTER, task, mem, "test_tracker_smoke_novel")
        assert updated is not None
        hot = updated.get("hot", updated)
        assert "protagonist_level" in hot


@test("验收标准 AC-2：题材切换")
def t17():
    from engine.tools.acceptance_tests import ac2_genre_switch
    result = ac2_genre_switch()
    assert result is True


@test("验收标准 AC-1：设定一致性（无设定包 → SKIP）")
def t18():
    from engine.tools.acceptance_tests import ac1_consistency
    result = ac1_consistency()
    assert result is True  # SKIP 也算 PASS


@test("预算管理：报告与警告")
def t19():
    from engine.tools.budget_manager import generate_report
    r = generate_report(500.0)
    assert "total_cost_usd" in r
    assert isinstance(r.get("budget_used_pct", 0), (int, float))


@test("状态持久化：热冷记忆")
def t20():
    from engine.state import create_initial_state, save_state, load_state
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    try:
        s = create_initial_state("persist_test", "测试书", "fanqie", "都市", "设定", 100.)
        s["current_chapter"] = 42
        s["quality_history"] = [7.5, 8.0, 6.8]
        save_state(s, tmp)
        loaded = load_state(tmp)
        assert loaded["current_chapter"] == 42
        assert loaded["quality_history"] == [7.5, 8.0, 6.8]
    finally:
        os.unlink(tmp)


@test("导出工具：无崩溃")
def t21():
    from engine.tools.exporter import get_chapter_list
    chs = get_chapter_list()
    assert isinstance(chs, list)


def run_all_tests() -> bool:
    print(f"\n{'═'*58}")
    print(f"  🧪 AI网文创作系统 — engine 集成测试 (P2/P3)")
    print(f"{'═'*58}\n")
    for t in (t1, t2, t3, t4, t5, t7, t8, t9, t10, t11, t12, t13, t14,
              t15, t16, t17, t18, t19, t20, t21):
        t()
    passed = sum(1 for r in results if r[0] == P)
    failed = sum(1 for r in results if r[0] == F)
    print(f"\n{'═'*58}")
    print(f"  结果：{passed}通过 / {failed}失败 / {len(results)}总计")
    if failed == 0:
        print(f"  🎉 全部通过！P2/P3 移植完成。")
    else:
        for icon, name, err in results:
            if icon == F:
                print(f"    {F} {name}: {err}")
    print(f"{'═'*58}\n")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)