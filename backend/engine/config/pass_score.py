"""engine/config/pass_score.py — §A4 校准闭环到 PASS_SCORE

背景（docs/drafts/architecture-roadmap-2026-07-27.md §A4）：
- 旧实现：PASS_SCORE=6.5 硬编码（orchestrator.py:67），从未被校准过
- calibrate_checker.run_calibration() 的输出和落盘 calibration_result.json
  都没有 `recommended_pass_score` 字段
- 校准测的是"人写/AI 写判别力" —— 与"什么分算通过"是两回事
- 当前 6.5 这条线从未被数据校准过

修法：
1. resolve_pass_score(calibration_path) 返回 (value, label)
   - 读到合法校准值 → ("calibrated", value钳到 [MIN, MAX])
   - 缺文件 / 损坏 / 字段缺失 / 类型非法 → ("default" | "default_calibration_corrupted",
     DEFAULT_PASS_SCORE)
   - **失败要响亮**：每条降级路径都 log.warning（CLAUDE.md 不变量）
2. orchestrator 路由时调 resolve_pass_score 取阈值（不再读模块常量）

不动的承诺：本文件不动 calibrate_checker.py 的内置样本与样本加载逻辑
（那个是另一回事，是校准集的扩展）。本轮只补"读出来 + 闭环到阈值"。
"""
from __future__ import annotations
import json
import logging
from pathlib import Path

_log = logging.getLogger("novel_ai.engine.config.pass_score")

# 评分制是 0..10，PASS_SCORE 钳制区间
DEFAULT_PASS_SCORE = 6.5
MIN_PASS_SCORE = 3.0   # 低于 3 等于"自动 PASS"，不合理
MAX_PASS_SCORE = 9.5   # 高于 9.5 等于"自动 FAIL"，不合理

# CalibrationResult 落盘的默认路径（与 calibrate_checker.RESULT_DIR 兼容）
DEFAULT_CALIBRATION_PATH = "output/reports/calibration_result.json"


def _clamp(value: float) -> float:
    return max(MIN_PASS_SCORE, min(MAX_PASS_SCORE, float(value)))


def resolve_pass_score(calibration_path: str | None) -> tuple[float, str]:
    """读 calibration_result.json，返回 (阈值, 来源标签)。

    label ∈ {"calibrated", "default", "default_calibration_corrupted"}
    - "calibrated" 真的读到推荐值（含钳制到合法区间）
    - "default" 缺文件或路径为 None —— 项目从未跑过校准
    - "default_calibration_corrupted" 校准文件存在但读不出 —— 文件损坏了
    """
    if calibration_path is None:
        _log.warning(
            "校准未运行，使用默认阈值 %.1f（uncalibrated default）",
            DEFAULT_PASS_SCORE,
        )
        return DEFAULT_PASS_SCORE, "default"

    p = Path(calibration_path)
    if not p.exists():
        _log.warning(
            "校准文件不存在 %s，使用默认阈值 %.1f（uncalibrated default）",
            calibration_path, DEFAULT_PASS_SCORE,
        )
        return DEFAULT_PASS_SCORE, "default"

    try:
        raw = p.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError("empty calibration file")
        data = json.loads(raw)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        # 损坏：响亮告警，降级到默认值 —— 静默吞会回到旧 bug
        _log.warning(
            "校准文件损坏 %s（%s: %s），降级到默认阈值 %.1f（default_calibration_corrupted）",
            calibration_path, type(e).__name__, e, DEFAULT_PASS_SCORE,
        )
        return DEFAULT_PASS_SCORE, "default_calibration_corrupted"

    if not isinstance(data, dict):
        _log.warning(
            "校准文件顶层不是 dict（%s），降级到默认（default_calibration_corrupted）",
            type(data).__name__,
        )
        return DEFAULT_PASS_SCORE, "default_calibration_corrupted"

    rec = data.get("recommended_pass_score")
    if rec is None:
        _log.warning(
            "校准文件缺字段 recommended_pass_score，降级到默认（default_calibration_corrupted）"
        )
        return DEFAULT_PASS_SCORE, "default_calibration_corrupted"

    # 类型校验：必须是数字（int 或 float）
    if not isinstance(rec, (int, float)) or isinstance(rec, bool):
        _log.warning(
            "校准文件 recommended_pass_score 类型非法（%s），降级到默认（default_calibration_corrupted）",
            type(rec).__name__,
        )
        return DEFAULT_PASS_SCORE, "default_calibration_corrupted"

    clamped = _clamp(float(rec))
    if clamped != float(rec):
        # 钳制不视为损坏：校准意图生效，只是数值被规约到合理区间
        _log.info(
            "校准阈值 %.2f 被钳制到 %.2f（区间 [%.1f, %.1f]）",
            rec, clamped, MIN_PASS_SCORE, MAX_PASS_SCORE,
        )
    return clamped, "calibrated"


__all__ = ["DEFAULT_PASS_SCORE", "MIN_PASS_SCORE", "MAX_PASS_SCORE",
           "DEFAULT_CALIBRATION_PATH", "resolve_pass_score"]