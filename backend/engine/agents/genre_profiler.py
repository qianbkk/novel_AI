"""genre_profiler.py — v1.0 Stage A 题材画像生成器

输入：genre_key（如 'xuanhuan' / 'xianxia' / 'dushi' / 'lishi' / 'junshi' / 'kehuan'）
输出：结构化 genre profile dict，落到 output/genre_profile.json

两种模式：
- use_llm=False（默认）：纯数据生成，模板直接作为 profile
- use_llm=True：模板作为 seed，LLM 在此基础上做"细化"（添加 extra_show_item /
  进一步收紧 persona 描述），但保留模板核心字段（research_strength / taboo 等）

CLAUDE.md 红线：
- 未知 genre_key → UnknownGenreError，不允许 silently fallback
- LLM 失败 → 不静默丢模板，回退到纯模板 + log.warning
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config.genre_profiles import (
    REQUIRED_PROFILE_FIELDS,
    get_genre_template,
)
from ..llm.router import LLMRouter  # noqa: F401  re-exported for callers
from ..llm_router import get_active_router
from ..utils import parse_llm_json_response


_log = logging.getLogger("novel_ai.engine.agents.genre_profiler")


class UnknownGenreError(ValueError):
    """未知题材 — v1.0 不允许 silently fallback。"""


# ════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════

def profile_genre(genre_key: str, *, use_llm: bool = False, novel_id: str | None = None) -> dict:
    """为指定 genre_key 生成题材画像。

    Args:
        genre_key: 6 个主流男频之一（'xuanhuan'/'xianxia'/'dushi'/'lishi'/'junshi'/'kehuan'）
        use_llm: 是否用 LLM 在模板基础上细化（默认 False，纯数据，CI 友好）
        novel_id: 项目 ID（用于落盘路径；不传则不落盘，只返回 dict）

    Returns:
        profile dict，含 REQUIRED_PROFILE_FIELDS 全部 7 个字段

    Raises:
        UnknownGenreError: genre_key 不在 6 个支持范围内
    """
    template = get_genre_template(genre_key)
    if template is None:
        raise UnknownGenreError(
            f"未知 genre_key {genre_key!r}，"
            f"支持：{sorted(['xuanhuan', 'xianxia', 'dushi', 'lishi', 'junshi', 'kehuan'])}。"
            "v1.0 不允许 silently fallback，新题材请先用 CLI 提交到 genre_profiles.py"
        )

    profile = _template_to_profile(template)

    if use_llm:
        profile = _refine_with_llm(profile, genre_key)

    if novel_id:
        _save_profile(profile, novel_id)

    return profile


def _template_to_profile(template: dict) -> dict:
    """模板 → profile dict（含 genre / genre_key / 7 个必填字段）。"""
    profile = {
        "genre": template["genre"],
        "genre_key": template["genre_key"],
        "reader_persona": dict(template["reader_persona"]),
        "tone_preference": str(template["tone_preference"]),
        "taboo": list(template["taboo"]),
        "show_item_examples": list(template["show_item_examples"]),
        "research_strength": str(template["research_strength"]),
    }
    return profile


def _refine_with_llm(profile: dict, genre_key: str) -> dict:
    """用 LLM 在模板基础上做细化（不覆盖核心字段）。

    LLM 失败 → 保留模板 + log.warning（CLAUDE.md '失败要响亮' 但细化失败不应阻断主线）
    """
    router = get_active_router()
    if router is None:
        _log.warning("LLM router 未初始化，跳过 genre profile 细化（保持模板）")
        return profile

    system_prompt = (
        "你是网文编辑，帮我细化题材画像。"
        "我会给你一份已含 reader_persona / taboo / show_item_examples 的模板，"
        "你只需要在原模板基础上做「细化」——增加 extra_show_item、"
        "让 reader_persona.primary 更具体；"
        "不要覆盖 research_strength（已经定了），不要删除 taboo，"
        "不要使用具体项目专名（角色名/地名/世界名），统一用'主角/配角/世界名'。"
        "直接输出 JSON，结构: "
        '{"reader_persona": {..., "extra": "..."}, "extra_show_item": "物件→情绪"}'
    )
    user_prompt = (
        f"题材：{profile['genre']} ({genre_key})\n"
        f"模板 reader_persona: {profile['reader_persona']}\n"
        f"现有 show_item_examples: {profile['show_item_examples']}"
    )

    try:
        out, cost = router.call(
            agent_name="genre_profiler",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=600,
            temperature=0.6,
        )
        refined = parse_llm_json_response(out, default={})
        if refined:
            # 合并：模板核心字段不动，LLM 字段叠加
            for k, v in refined.items():
                if k == "reader_persona" and isinstance(v, dict):
                    # persona 字段合并而非覆盖
                    profile["reader_persona"].update(v)
                elif k == "show_item_examples" and isinstance(v, list):
                    # LLM 给的新示例追加（不覆盖）
                    profile["show_item_examples"].extend(v)
                else:
                    # extra_show_item / 其它字段直接加
                    profile[k] = v
            _log.info("genre_profiler LLM 细化完成 genre=%s cost=%.4f", genre_key, cost)
        else:
            _log.warning("genre_profiler LLM 输出无法解析，保持模板（genre=%s）", genre_key)
    except Exception as exc:
        _log.warning("genre_profiler LLM 细化失败 genre=%s err=%s — 保持模板", genre_key, exc)

    return profile


def _call_llm_refine(*args, **kwargs):  # pragma: no cover  — 测试可 monkeypatch
    """供测试 monkeypatch 的占位 LLM 调用（默认走 router.call）。"""
    raise NotImplementedError("应通过 router.call 或 monkeypatch 调用")


# 2026-08-18 修复（CLAUDE.md「失败要响亮」）：删除 _parse_llm_json wrapper。
# utils.parse_llm_json_response 本身已经 log + 处理失败返 default；
# 包一层 except Exception: return None 反而吞了 import 失败和潜在 bug。
# 直接调 utils.parse_llm_json_response 即可。


def _save_profile(profile: dict, novel_id: str) -> None:
    """落盘到 <novel_ai_dir>/output/genre_profile.json（与 STATE_PATH 同根）。"""
    from ..config.paths import novel_ai_dir
    import json as _json

    base = Path(novel_ai_dir(novel_id)) / "output"
    base.mkdir(parents=True, exist_ok=True)
    target = base / "genre_profile.json"
    target.write_text(_json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("genre profile 落盘: %s", target)


def load_profile(novel_id: str) -> dict | None:
    """加载已落盘的 genre profile（v1.0 老项目 bootstrap 时若存在则跳过初始化）。

    2026-08-18 修复（CLAUDE.md「失败要响亮」）：之前 `except Exception: return None`
    无任何 log — 磁盘损坏 / 权限错误 / 编码错误都静默吞掉，
    跟 normalizer 修复（commit cd57dfd）同样的反模式。
    修法：catch 后调 log.exception，留 traceback 但仍返 None
    （调用方按"文件不存在 / 损坏"语义处理是合理的）。
    """
    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "genre_profile.json"
    if not target.is_file():
        return None
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        for f_name in REQUIRED_PROFILE_FIELDS:
            if f_name not in data:
                _log.warning("genre profile 缺字段 %s，视为损坏: %s", f_name, target)
                return None
        return data
    except Exception:
        _log.exception("load_profile 读取失败（将视作无 profile）: %s", target)
        return None