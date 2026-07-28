"""test_pass_score_calibration_2026_07_27.py

架构审视 §A4 — 校准闭环到 PASS_SCORE。

真实缺口：
- `engine/orchestrator.py:67` 把 `PASS_SCORE = 6.5` 写死成模块常量。
- `engine/tools/calibrate_checker.py:182` 把校准结果落盘到
  `calibration_result.json`，但 `run_calibration()` 的输出和落盘文件都
  没有这个字段；也没有任何代码读它。
- 校准测的是"人写 / AI 写判别力"（accuracy on a labelled sample set），
  与"什么分算通过"是两件事 —— 现在 6.5 这条线从未被人 / 数据校准过。

本轮修法（spec）：
1. `run_calibration()` 输出增加 `recommended_pass_score`：由人工标注
   分数分布推导（例如取"可接受"与"不可接受"分界，或者标注者认为刚好
   及格的若干样本的 score 中位数）。落盘的 `calibration_result.json`
   必须含此字段。
2. 引入 `engine.config.pass_score.resolve_pass_score(calibration_path)`：
   - 读到合法的 `recommended_pass_score` → 返回 `(value, "calibrated")`，
     并把值钳制到合理区间 `[MIN_PASS_SCORE, MAX_PASS_SCORE]`。
   - 缺文件 / 文件损坏 / 字段缺失 / 值越界 → 返回 `(DEFAULT_PASS_SCORE,
     "default" | "default_calibration_corrupted")` 并 log.warning。
3. `orchestrator.route_after_pipeline` / `route_after_rewrite` 改读
   `resolve_pass_score(...)` 的结果（不再硬编码 6.5）。

本文件是 **spec 定义**，所有断言引用**待实现**符号：实现落地前
pytest collect 会因为 `engine.config.pass_score` 模块不存在而整体失败，
这是预期的 —— 这是任务书，答案在实现里。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

# ── 待实现符号 ─────────────────────────────────────────────
from engine.config.pass_score import (  # noqa: E402  -- spec imports
    DEFAULT_PASS_SCORE,
    MAX_PASS_SCORE,
    MIN_PASS_SCORE,
    resolve_pass_score,
)


# ─── 1. 常量契约 ─────────────────────────

def test_default_pass_score_constant_value():
    """本轮 spec：硬编码 6.5 已经移除，DEFAULT 必须精确等于 6.5。
    任何改动都得同时改这个断言，不要静默偏移。"""
    assert DEFAULT_PASS_SCORE == 6.5


def test_pass_score_bounds_are_sane():
    """MIN / MAX 区间留给后来的"钳制"测试用 —— 这一组断言保证
    上下限本身没被写颠倒或写成离奇值。"""
    assert MIN_PASS_SCORE < DEFAULT_PASS_SCORE < MAX_PASS_SCORE
    assert MIN_PASS_SCORE >= 0.0
    assert MAX_PASS_SCORE <= 10.0  # 评分制就是 0..10


def test_resolve_pass_score_returns_tuple_of_float_and_label():
    """接口契约：返回 (float, str) 二元组。两个位置类型不能换。"""
    value, label = resolve_pass_score(None)
    assert isinstance(value, float)
    assert isinstance(label, str)


# ─── 2. 缺文件 → 默认值 + 告警 ─────────────────────────

def test_no_calibration_file_returns_default(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.config.pass_score")
    value, label = resolve_pass_score(str(tmp_path / "does_not_exist.json"))
    assert label == "default"
    assert value == DEFAULT_PASS_SCORE
    # 必须有"未校准"语义告警 —— 静默吞掉就是回到老 bug
    assert any("未校准" in rec.getMessage() or "uncalibrated" in rec.getMessage().lower()
               for rec in caplog.records if rec.levelno >= logging.WARNING), \
        f"应记录一条带「未校准」语义的 WARNING，实际记录: " \
        f"{[r.getMessage() for r in caplog.records]}"


def test_none_path_returns_default_with_warning(caplog):
    """传入 None 也走默认路径（与生产链路上"还没跑过校准"等同）。"""
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.config.pass_score")
    value, label = resolve_pass_score(None)
    assert (value, label) == (DEFAULT_PASS_SCORE, "default")


# ─── 3. 校准文件 → 使用校准值 ─────────────────────────

def _write_calibration(path: Path, *, recommended: float,
                       extra: dict | None = None) -> Path:
    payload = {"checkers": {}, "passed": True, "total_cost": 0.0,
               "recommended_pass_score": recommended}
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_valid_calibration_returns_calibrated_value(tmp_path):
    """核心断言：PASS_SCORE 读到 7.0 不是 6.5。这正是 §A4 修法想验证的
    行为变化 —— 校准值真的进了决策而不是被忽略。"""
    cfg = _write_calibration(tmp_path / "calibration_result.json", recommended=7.0)
    value, label = resolve_pass_score(str(cfg))
    assert label == "calibrated"
    assert value == pytest.approx(7.0)


def test_calibration_value_at_lower_bound_does_not_clamp_to_default(tmp_path):
    """合法校准值即使刚好在 MIN 区附近也不应被当成"损坏"。
    校准出 5.5 跟默认 6.5 不一样，必须真传出去。"""
    cfg = _write_calibration(tmp_path / "calibration_result.json", recommended=5.5)
    value, label = resolve_pass_score(str(cfg))
    assert value == pytest.approx(5.5)
    assert label == "calibrated"


def test_calibration_file_missing_recommended_field_falls_back(tmp_path, caplog):
    """校准文件结构对（合法 JSON）但缺字段：spec 上仍走默认 + 告警，
    标签用 'default_calibration_corrupted'（"字段缺失"是损坏的一种）。"""
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.config.pass_score")
    p = tmp_path / "calibration_result.json"
    p.write_text(json.dumps({"checkers": {}, "passed": True}), encoding="utf-8")
    value, label = resolve_pass_score(str(p))
    assert value == DEFAULT_PASS_SCORE
    assert label == "default_calibration_corrupted"


# ─── 4. 损坏文件 → 默认值 + 告警，不崩 ─────────────────────────

def test_corrupt_calibration_file_does_not_crash(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.config.pass_score")
    p = tmp_path / "calibration_result.json"
    p.write_text("{not even valid json", encoding="utf-8")
    value, label = resolve_pass_score(str(p))
    assert label == "default_calibration_corrupted"
    assert value == DEFAULT_PASS_SCORE
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records), \
        "损坏文件必须告警，不能静默回到默认"


def test_empty_calibration_file_does_not_crash(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.config.pass_score")
    p = tmp_path / "calibration_result.json"
    p.write_text("", encoding="utf-8")
    value, label = resolve_pass_score(str(p))
    assert label == "default_calibration_corrupted"
    assert value == DEFAULT_PASS_SCORE


# ─── 5. 钳制 ─────────────────────────

def test_extreme_high_recommended_is_clamped(tmp_path):
    """校准文件声称阈值 99 → 必须钳回 MAX（不能放过，否则所有章节都不过）。"""
    cfg = _write_calibration(tmp_path / "calibration_result.json", recommended=99.0)
    value, label = resolve_pass_score(str(cfg))
    assert value == pytest.approx(MAX_PASS_SCORE)
    assert label == "calibrated"  # 钳制仍算校准生效


def test_extreme_low_recommended_is_clamped(tmp_path):
    """校准阈值给 0 → 钳到 MIN，否则所有垃圾都自动 PASS。"""
    cfg = _write_calibration(tmp_path / "calibration_result.json", recommended=0.0)
    value, label = resolve_pass_score(str(cfg))
    assert value == pytest.approx(MIN_PASS_SCORE)
    assert label == "calibrated"


def test_non_numeric_recommended_falls_back_to_default(tmp_path, caplog):
    """非法类型：str / None / list —— 不是 clamp 的事，是损坏的另一面。
    spec 走 default_calibration_corrupted，不抛。"""
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.config.pass_score")
    cfg = _write_calibration(tmp_path / "calibration_result.json",
                             recommended="应该是数字")  # type: ignore[arg-type]
    value, label = resolve_pass_score(str(cfg))
    assert label == "default_calibration_corrupted"
    assert value == DEFAULT_PASS_SCORE


# ─── 6. 阈值变化真改变 orchestrator 路由 ─────────────────────────

def _route_state(score: float, rewrites: int = 0) -> dict:
    """构造一个 orchestrator route_after_pipeline 期望的最小 state。"""
    return {
        "current_phase": None,
        "current_task": {
            "_checker_result": {"score": score, "verdict": "PASS" if score >= 6.5 else "FAIL"},
            "_writer_failed": False,
            "_compliance_check_failed": False,
            "_checker_failed": False,
            "_compliance_failed": False,
        },
        "rewrite_count_current": rewrites,
    }


def _route_with_threshold(monkeypatch, threshold: float):
    """把 orchestrator 模块里的 PASS_SCORE 替换成目标阈值，
    然后调 route_after_pipeline 路由函数。"""
    import engine.orchestrator as orch
    monkeypatch.setattr(orch, "PASS_SCORE", threshold)
    return orch.route_after_pipeline


def test_score_below_threshold_with_rewrites_left_goes_rewrite(monkeypatch, tmp_path):
    """校准把阈值抬到 7.5：score=7.0（旧默认下会 PASS，新阈值下 FAIL）→ rewrite。"""
    cfg = _write_calibration(tmp_path / "calibration_result.json", recommended=7.5)
    value, _ = resolve_pass_score(str(cfg))
    route = _route_with_threshold(monkeypatch, value)
    decision = route(_route_state(score=7.0, rewrites=0))
    assert decision == "rewrite", f"score=7.0 < threshold=7.5 应 rewrite，实际 {decision}"


def test_score_at_or_above_threshold_goes_save(monkeypatch, tmp_path):
    """边界值：score == threshold 必须走 save（route 是 >= 比较，不是 >）。"""
    cfg = _write_calibration(tmp_path / "calibration_result.json", recommended=7.0)
    value, _ = resolve_pass_score(str(cfg))
    route = _route_with_threshold(monkeypatch, value)
    assert route(_route_state(score=7.0, rewrites=0)) == "save"
    assert route(_route_state(score=7.5, rewrites=0)) == "save"


def test_score_below_threshold_with_rewrites_exhausted_goes_escalate(monkeypatch, tmp_path):
    """阈值抬高 + rewrite 已耗尽 → escalate（不是死循环 rewrite）。"""
    cfg = _write_calibration(tmp_path / "calibration_result.json", recommended=7.5)
    value, _ = resolve_pass_score(str(cfg))
    route = _route_with_threshold(monkeypatch, value)
    decision = route(_route_state(score=7.0, rewrites=3))
    assert decision == "escalate", f"rewrites 耗尽 + 不达标应 escalate，实际 {decision}"


def test_default_threshold_decision_changes_with_score(monkeypatch):
    """对照组：默认阈值下 score=6.5 必须 save，score=6.4 必须 rewrite / escalate。
    这一组断言对照上面 3 个 —— 证明差异真的来自 PASS_SCORE 而不是别处。"""
    route = _route_with_threshold(monkeypatch, DEFAULT_PASS_SCORE)
    assert route(_route_state(score=6.5, rewrites=0)) == "save"
    assert route(_route_state(score=6.4, rewrites=0)) == "rewrite"
    assert route(_route_state(score=6.4, rewrites=3)) == "escalate"


# ─── 7. run_calibration 输出契约 ─────────────────────────

def test_run_calibration_output_includes_recommended_pass_score(monkeypatch, tmp_path):
    """run_calibration() 落盘前必须带 recommended_pass_score 字段，
    否则 orchestrator 那侧没东西可读。

    这条断言盯的是 calibrate_checker.run_calibration() 不是 resolve_pass_score，
    跟 §A4 修法第 1 条对应。"""
    from engine.tools import calibrate_checker as cc

    # 把落盘位置钉到 tmp_path，避免污染生产 OUT_DIR
    monkeypatch.setattr(cc, "RESULT_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "_load_samples", lambda: [])  # 不发真 LLM 请求
    # score_sample 不发真请求——给一个恒定的 fake
    monkeypatch.setattr(cc, "score_sample",
                        lambda text, checker: (8.0, "fake", 0.0))

    out = cc.run_calibration()
    assert "recommended_pass_score" in out, \
        f"run_calibration 输出必须含 recommended_pass_score，实际 keys: {list(out.keys())}"
    assert isinstance(out["recommended_pass_score"], (int, float))
    # 写入磁盘后也要有这个字段
    on_disk = json.loads((tmp_path / "calibration_result.json").read_text(encoding="utf-8"))
    assert "recommended_pass_score" in on_disk
