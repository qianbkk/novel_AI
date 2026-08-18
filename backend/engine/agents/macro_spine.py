"""macro_spine.py - v1.0 Stage E 全书宏观弧结构

输入：theme_spine + opening_design + total_chapters
输出：macro_spine（落 output/macro_spine.json）

核心思想：
- 一本长篇小说 = 多个 arc 串成，每个 arc 8-20 章
- 每个 arc 有独立 theme_focus + main_conflict + expectation_progress + tone
- arc 边界必须连续（arc[i].end_chapter + 1 == arc[i+1].start_chapter）
- arc 数量在 [2, 10] 之间（太少 = 单调，太多 = 节奏碎）

CLAUDE.md 红线：
- 必填字段缺失 → InvalidMacroSpineError（不让半成品落盘）
- arc 边界不能重叠 → InvalidMacroSpineError
- arc 数量 < 2 → InvalidMacroSpineError
- 不含具体项目专名
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..llm_router import get_active_router
from ..utils import parse_llm_json_response


_log = logging.getLogger("novel_ai.engine.agents.macro_spine")


REQUIRED_MACRO_FIELDS = ("arcs", "total_chapters", "source")
REQUIRED_ARC_FIELDS = (
    "arc_id", "name", "start_chapter", "end_chapter",
    "theme_focus", "main_conflict", "expectation_progress", "tone",
)
MIN_ARCS = 2
MAX_ARCS = 10


class InvalidMacroSpineError(ValueError):
    """macro_spine 字段不全或结构错误。"""


# ── 模板生成（按 total_chapters 划分 arc 数）─────────────────

def _plan_arc_layout(total_chapters: int) -> list[tuple[int, int]]:
    """按 total_chapters 规划 arc 边界，返回 [(start, end), ...] 列表。"""
    if total_chapters <= 20:
        # 2 arcs
        mid = total_chapters // 2
        return [(1, mid), (mid + 1, total_chapters)]
    elif total_chapters <= 50:
        # 3 arcs
        e1 = total_chapters // 3
        e2 = 2 * total_chapters // 3
        return [(1, e1), (e1 + 1, e2), (e2 + 1, total_chapters)]
    elif total_chapters <= 80:
        # 4 arcs（开局 / 主体前 / 主体后 / 收束）
        e1 = total_chapters // 5
        e2 = total_chapters // 2
        e3 = 4 * total_chapters // 5
        return [(1, e1), (e1 + 1, e2), (e2 + 1, e3), (e3 + 1, total_chapters)]
    else:
        # 5 arcs（长篇）
        unit = total_chapters // 5
        return [
            (1, unit),
            (unit + 1, 2 * unit),
            (2 * unit + 1, 3 * unit),
            (3 * unit + 1, 4 * unit),
            (4 * unit + 1, total_chapters),
        ]


_ARC_NAME_BY_INDEX = {
    1: "开局",
    2: "发展",
    3: "转折",
    4: "高潮",
    5: "收束",
    6: "延伸",
    7: "深化",
    8: "破局",
    9: "归一",
    10: "终章",
}


# ── Public API ─────────────────────────────────────────────

def design_macro_spine(
    theme_spine: dict,
    opening_design: dict,
    total_chapters: int,
    *,
    use_llm: bool = False,
    novel_id: str | None = None,
) -> dict:
    """设计全书宏观弧结构（macro_spine）。

    Args:
        theme_spine: 来自 theme_designer 的 spine dict
        opening_design: 来自 opening_designer 的 dict（含期望种子）
        total_chapters: 总章节数（用户设定 / 默认 80）
        use_llm: 是否用 LLM 在模板基础上细化（CI 友好默认 False）
        novel_id: 项目 ID（落盘用）

    Returns:
        macro_spine dict，含 arcs + total_chapters + source
    """
    layout = _plan_arc_layout(total_chapters)
    spine = _build_template(theme_spine, opening_design, layout, total_chapters, source="template")

    if use_llm:
        spine = _refine_with_llm(spine, theme_spine, opening_design, total_chapters)

    if novel_id:
        save_macro_spine(novel_id, spine)

    return spine


def save_macro_spine(novel_id: str, spine: dict) -> None:
    """落盘 macro_spine。

    Raises:
        InvalidMacroSpineError: 缺字段 / 类型错 / arc 边界重叠 / arc 数量非法
    """
    if not isinstance(spine, dict):
        raise InvalidMacroSpineError(f"macro_spine 必须是 dict，实际 {type(spine).__name__}")

    for f in REQUIRED_MACRO_FIELDS:
        if f not in spine:
            raise InvalidMacroSpineError(
                f"macro_spine 缺字段 {f!r}；落盘前必须含 {REQUIRED_MACRO_FIELDS}"
            )

    arcs = spine["arcs"]
    if not isinstance(arcs, list):
        raise InvalidMacroSpineError(
            f"arcs 必须是 list，实际 {type(arcs).__name__}"
        )
    if len(arcs) < MIN_ARCS:
        raise InvalidMacroSpineError(
            f"arc 数量必须 >= {MIN_ARCS}，实际 {len(arcs)}（arc 太少 = 节奏单调）"
        )
    if len(arcs) > MAX_ARCS:
        raise InvalidMacroSpineError(
            f"arc 数量必须 <= {MAX_ARCS}，实际 {len(arcs)}（arc 太多 = 节奏碎）"
        )

    for a in arcs:
        for f in REQUIRED_ARC_FIELDS:
            if f not in a:
                raise InvalidMacroSpineError(
                    f"arc 缺字段 {f!r}；必填 {REQUIRED_ARC_FIELDS}"
                )

    # arc 边界连续性 + 不能超出 total_chapters
    total = spine["total_chapters"]
    if arcs[0]["start_chapter"] != 1:
        raise InvalidMacroSpineError(
            f"第一个 arc 必须从 chapter 1 开始，实际 start={arcs[0]['start_chapter']}"
        )
    if arcs[-1]["end_chapter"] != total:
        raise InvalidMacroSpineError(
            f"最后一个 arc 必须到 chapter {total} 结束，实际 end={arcs[-1]['end_chapter']}"
        )
    for i in range(len(arcs) - 1):
        if arcs[i]["end_chapter"] + 1 != arcs[i + 1]["start_chapter"]:
            raise InvalidMacroSpineError(
                f"arc {i+1} end={arcs[i]['end_chapter']} 与 arc {i+2} "
                f"start={arcs[i+1]['start_chapter']} 不连续"
            )
        if arcs[i]["start_chapter"] > arcs[i]["end_chapter"]:
            raise InvalidMacroSpineError(
                f"arc {i+1} start > end：{arcs[i]['start_chapter']} > {arcs[i]['end_chapter']}"
            )

    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "macro_spine.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    target.write_text(_json.dumps(spine, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("macro_spine 落盘: %s (%d arcs, total=%d)",
              target, len(arcs), total)


def load_macro_spine(novel_id: str) -> dict | None:
    """加载已落盘的 macro_spine（启动时检测，老项目 bootstrap 按 default 初始化）。

    2026-08-18 修复（CLAUDE.md「失败要响亮」）：见 genre_profiler.load_profile 注释。
    """
    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "macro_spine.json"
    if not target.is_file():
        return None
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        for f_name in REQUIRED_MACRO_FIELDS:
            if f_name not in data:
                _log.warning("macro_spine 缺字段 %s，视为损坏: %s", f_name, target)
                return None
        return data
    except Exception:
        _log.exception("load_macro_spine 读取失败（将视作无 spine）: %s", target)
        return None


def get_arc_for_chapter(macro_spine: dict, chapter_number: int) -> dict | None:
    """按章号查所属 arc（orchestrator / writer 写每章前调一次）。"""
    if not macro_spine:
        return None
    for a in macro_spine.get("arcs", []):
        if a["start_chapter"] <= chapter_number <= a["end_chapter"]:
            return a
    return None


# ── Internal helpers ─────────────────────────────────────────────

def _build_template(
    theme_spine: dict,
    opening_design: dict,
    layout: list[tuple[int, int]],
    total_chapters: int,
    *,
    source: str,
) -> dict:
    """按 layout + theme 构建 macro_spine 模板。"""
    theme_stmt = (theme_spine or {}).get("theme_statement", "") or "（未指定主题）"
    arc = (theme_spine or {}).get("expectation_arc", {}) or {}
    twist = arc.get("twist_chapter", total_chapters // 4)
    payoff = arc.get("payoff_chapter", total_chapters)

    arcs: list[dict] = []
    for i, (start, end) in enumerate(layout):
        # 期待感推进：第 1 arc 强化 seed，中间弧推进，第 4+ arc 接近 payoff
        if i == 0:
            progress = "seed 强化 + 建立主线"
            tone = "克制 / 期待"
        elif i == len(layout) - 1:
            progress = f"payoff（接近 chapter {payoff}）"
            tone = "释然 / 收束"
        elif start <= twist <= end:
            progress = "twist 推进（主角重新定义'主题'）"
            tone = "震荡 / 重估"
        else:
            progress = f"主线推进（朝 chapter {payoff} 演进）"
            tone = "发展 / 推进"

        arcs.append({
            "arc_id": i + 1,
            "name": _ARC_NAME_BY_INDEX.get(i + 1, f"弧{i+1}"),
            "start_chapter": start,
            "end_chapter": end,
            "theme_focus": f"围绕'{theme_stmt[:20]}'的 {['建立', '强化', '考验', '推进', '收束'][min(i, 4)]}",
            "main_conflict": f"（待 LLM 填充 / 用户编辑）{theme_stmt[:30]} 在本弧的核心冲突",
            "expectation_progress": progress,
            "tone": tone,
        })

    return {
        "arcs": arcs,
        "total_chapters": total_chapters,
        "source": source,
    }


def _refine_with_llm(spine: dict, theme_spine: dict, opening_design: dict, total: int) -> dict:
    """用 LLM 在模板基础上细化每个 arc 的 name / theme_focus / main_conflict。

    LLM 失败 → 保留模板 + log.warning。
    """
    router = get_active_router()
    if router is None:
        _log.warning("LLM router 未初始化，跳过 macro_spine 细化（保持模板）")
        return spine

    theme_stmt = (theme_spine or {}).get("theme_statement", "")
    system_prompt = (
        "你是长篇小说策划，帮我细化全书宏观弧。"
        f"我会给你：主题（{theme_stmt}）+ 模板 {len(spine['arcs'])} 个 arc。"
        "你需要：每个 arc 改得更具体（name / theme_focus / main_conflict）"
        "约束：不含具体项目专名（角色名/地名/世界名），"
        "统一用'主角/配角/世界名'中性词。"
        "直接输出 JSON: {\"arcs\": [{arc_id, name, theme_focus, main_conflict, ...}, ...]}"
    )
    user_prompt = f"模板：{spine}"

    try:
        out, cost = router.call(
            agent_name="macro_spine",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1000,
            temperature=0.7,
        )
        refined = parse_llm_json_response(out, default={})
        if refined and isinstance(refined.get("arcs"), list):
            for llm_arc, tmpl_arc in zip(refined["arcs"], spine["arcs"]):
                if not isinstance(llm_arc, dict):
                    continue
                for k in ("name", "theme_focus", "main_conflict"):
                    v = llm_arc.get(k)
                    if isinstance(v, str) and v:
                        tmpl_arc[k] = v
            spine["source"] = "llm"
            _log.info("macro_spine LLM 细化完成 cost=%.4f", cost)
            return spine
        else:
            _log.warning("macro_spine LLM 输出无法解析，保持模板")
            return spine
    except Exception as exc:
        _log.warning("macro_spine LLM 细化失败 err=%s — 保持模板", exc)
        return spine


# 2026-08-18 修复（CLAUDE.md「失败要响亮」）：删除 _parse_llm_json wrapper。
# utils.parse_llm_json_response 本身已 log + 处理失败；wrapper 反而吞了 import error。