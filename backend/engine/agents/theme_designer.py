"""theme_designer.py - v1.0 Stage B universal theme + expectation arc designer.

Inputs: concept + genre_profile + key_characters
Outputs: theme_spine (saved to output/theme_spine.json)

Core fields (docs/drafts/v1-quality-first-design.md section Stage 1b):
- theme_statement: 1-sentence universal theme
- expectation_arc: seed_chapter / payoff_chapter / twist_chapter / description
- resonance_anchors: >=3 universal resonance dimensions
- source: 'template' / 'llm' / 'user' (provenance tag)

CLAUDE.md rules:
- Missing required field -> InvalidThemeError (no half-baked data on disk)
- LLM failure -> keep template + log.warning (refinement must not block main path)
- No project proper-nouns in prompts/templates
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..llm_router import get_active_router


_log = logging.getLogger("novel_ai.engine.agents.theme_designer")


REQUIRED_THEME_FIELDS = (
    "theme_statement",
    "expectation_arc",
    "resonance_anchors",
    "source",
)


# ── 异常 ─────────────────────────────────────────────

class InvalidThemeError(ValueError):
    """theme_spine 字段不全或结构错误。"""


# ── 6 个题材的 theme 种子模板 ─────────────────────────
# 每个 genre 用不同的'种子' theme_statement + resonance_anchors，
# LLM 在此基础上改写。模板本身就有结构性（3 字段齐全）。

_GENRE_THEME_TEMPLATES: dict[str, dict] = {
    "xuanhuan": {
        "theme_statement": "被低估的少年在觉醒中找到'我是谁'的答案",
        "expectation_arc": {
            "seed_chapter": 1,
            "payoff_chapter": 60,
            "twist_chapter": 20,
            "description": "读者在 ch1 期待主角觉醒血脉；ch20 主线围绕'血脉不是命运'展开；ch60 主角重新定义'觉醒' = 不是成为最强而是成为自己",
        },
        "resonance_anchors": [
            "真正的强大不是让别人怕你",
            "血脉给的起点不等于终点",
            "被低估本身就是一种保护（让你有空间成长）",
        ],
    },
    "xianxia": {
        "theme_statement": "修仙路上真正的道心，是能否在劫波中守住'我还是我'",
        "expectation_arc": {
            "seed_chapter": 1,
            "payoff_chapter": 80,
            "twist_chapter": 25,
            "description": "读者在 ch1 期待主角飞升；ch25 主角必须接受'飞升 ≠ 证道'；ch80 主角以'不飞升也能证道'完成内心闭环",
        },
        "resonance_anchors": [
            "长生不是目的，记住你是谁才是",
            "道心不是不会动摇，是动摇了还能回去",
            "修仙修到最后，修的是自己的选择",
        ],
    },
    "dushi": {
        "theme_statement": "在钢筋水泥的丛林里，能让人重新出发的不是机会，是自己心里那盏灯",
        "expectation_arc": {
            "seed_chapter": 1,
            "payoff_chapter": 50,
            "twist_chapter": 18,
            "description": "读者在 ch1 期待主角逆袭翻身；ch18 主角意识到'翻身不是终点'；ch50 主角用另一种方式定义'赢'",
        },
        "resonance_anchors": [
            "穷不是没钱的代名词，是看不到出路",
            "翻盘不只是赚钱，是重新成为想成为的人",
            "真心不是说出来，是在最难的时候做了什么",
        ],
    },
    "lishi": {
        "theme_statement": "在大时代里，普通人能守住的只有'回家'这一件事",
        "expectation_arc": {
            "seed_chapter": 1,
            "payoff_chapter": 80,
            "twist_chapter": 25,
            "description": "读者在 ch1 期待主角回家；ch25 主角必须接受'回家 ≠ 团圆'；ch80 主角用另一种方式定义'回家'",
        },
        "resonance_anchors": [
            "家不只是一个地址，是你想回去成为的那个人",
            "忠诚不是'我为你死'，是'我替你记得'",
            "乱世里最奢侈的不是活着，是有人等你",
        ],
    },
    "junshi": {
        "theme_statement": "战场上能让人活下来的不是武器，是身边有没有愿意护你的人",
        "expectation_arc": {
            "seed_chapter": 1,
            "payoff_chapter": 60,
            "twist_chapter": 22,
            "description": "读者在 ch1 期待主角立功；ch22 主角意识到'立功不是终点'；ch60 主角用另一种方式定义'战士'",
        },
        "resonance_anchors": [
            "战友情不是同年同月生，是同年同月死",
            "真正的勇敢不是不怕，是怕了还站着",
            "活着不是为了杀敌，是为了战友不再死",
        ],
    },
    "kehuan": {
        "theme_statement": "在宇宙尺度上重新发现'人是什么'，答案不在星辰，在自己",
        "expectation_arc": {
            "seed_chapter": 1,
            "payoff_chapter": 70,
            "twist_chapter": 24,
            "description": "读者在 ch1 期待主角解答宇宙谜题；ch24 主角意识到'谜题'本身就是答案的一部分；ch70 主角不再向外求",
        },
        "resonance_anchors": [
            "宇宙没有意志，只有规律",
            "孤独不是没人陪，是没人懂",
            "文明的差距不是技术，是看待'人'的方式",
        ],
    },
}


# ── Public API ─────────────────────────────────────────────

def design_theme(
    concept: str,
    genre_profile: dict,
    key_characters: list[dict],
    *,
    use_llm: bool = False,
    novel_id: str | None = None,
) -> dict:
    """Design theme_spine for given project.

    Args:
        concept: 用户的初始概念（一句话）
        genre_profile: 来自 genre_profiler 的 profile dict
        key_characters: 项目 key_characters（用于 LLM 校验 theme 是否覆盖主角关系）
        use_llm: 是否用 LLM 在模板基础上改写（CI 友好默认 False）
        novel_id: 项目 ID（落盘用）

    Returns:
        theme_spine dict，含 REQUIRED_THEME_FIELDS 全部 4 个字段
    """
    template = _pick_template(genre_profile)

    theme = _template_to_theme(template, source="template")

    if use_llm:
        theme = _refine_with_llm(theme, concept, genre_profile, key_characters)

    if novel_id:
        save_theme(novel_id, theme)

    return theme


def save_theme(novel_id: str, theme: dict) -> None:
    """落盘 theme_spine（用户编辑或 AI 生成都走这条路径）。

    Args:
        novel_id: 项目 ID
        theme: 完整 theme_spine dict

    Raises:
        InvalidThemeError: 缺字段 / 类型错 / theme_statement 空
    """
    if not isinstance(theme, dict):
        raise InvalidThemeError(f"theme_spine 必须是 dict，实际 {type(theme).__name__}")

    for f in REQUIRED_THEME_FIELDS:
        if f not in theme:
            raise InvalidThemeError(
                f"theme_spine 缺字段 {f!r}；落盘前必须含全部 {REQUIRED_THEME_FIELDS}"
            )

    if not isinstance(theme["theme_statement"], str) or not theme["theme_statement"].strip():
        raise InvalidThemeError(
            f"theme_statement 必须是非空字符串，实际 {theme['theme_statement']!r}"
        )

    arc = theme["expectation_arc"]
    if not isinstance(arc, dict):
        raise InvalidThemeError(
            f"expectation_arc 必须是 dict，实际 {type(arc).__name__}"
        )
    for k in ("seed_chapter", "payoff_chapter", "twist_chapter", "description"):
        if k not in arc:
            raise InvalidThemeError(f"expectation_arc 缺字段: {k!r}")

    if arc["seed_chapter"] >= arc["twist_chapter"] or arc["twist_chapter"] >= arc["payoff_chapter"]:
        raise InvalidThemeError(
            f"expectation_arc 节点必须 seed < twist < payoff，"
            f"实际 seed={arc['seed_chapter']}, twist={arc['twist_chapter']}, "
            f"payoff={arc['payoff_chapter']}"
        )

    anchors = theme["resonance_anchors"]
    if not isinstance(anchors, list) or len(anchors) < 3:
        raise InvalidThemeError(
            f"resonance_anchors 必须是 ≥3 条 list，实际 {anchors!r}"
        )

    from ..config.paths import novel_ai_dir
    from ..utils import atomic_write_json

    target = Path(novel_ai_dir(novel_id)) / "output" / "theme_spine.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    # atomic_write_json 不支持 indent 参数，直接用 json.dump + ensure_ascii=False
    import json as _json
    target.write_text(_json.dumps(theme, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("theme_spine 落盘: %s (source=%s)", target, theme.get("source"))


def load_theme(novel_id: str) -> dict | None:
    """加载已落盘的 theme_spine（启动时检测，老项目 bootstrap 自动按 default 初始化）。"""
    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "theme_spine.json"
    if not target.is_file():
        return None
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        # 完整性校验，损坏视为 None
        for f_name in REQUIRED_THEME_FIELDS:
            if f_name not in data:
                _log.warning("theme_spine 缺字段 %s，视为损坏: %s", f_name, target)
                return None
        return data
    except Exception:
        return None


# ── Internal helpers ─────────────────────────────────────────────

def _pick_template(genre_profile: dict) -> dict:
    """按 genre_profile.genre_key 选种子模板（缺 genre_key → 走通用 fallback）。"""
    key = (genre_profile or {}).get("genre_key") or ""
    return dict(_GENRE_THEME_TEMPLATES.get(
        key,
        _GENRE_THEME_TEMPLATES["dushi"],  # 通用 fallback（最通用题材）
    ))


def _template_to_theme(template: dict, *, source: str) -> dict:
    """模板 → theme dict（含 source 标注）。"""
    return {
        "theme_statement": str(template["theme_statement"]),
        "expectation_arc": dict(template["expectation_arc"]),
        "resonance_anchors": list(template["resonance_anchors"]),
        "source": source,
    }


def _refine_with_llm(
    theme: dict,
    concept: str,
    genre_profile: dict,
    key_characters: list[dict],
) -> dict:
    """用 LLM 改写 theme_spine（不破坏结构性字段）。

    LLM 失败 → 保留模板 + log.warning。
    LLM 字段缺失 → 用模板字段兜底。
    """
    router = get_active_router()
    if router is None:
        _log.warning("LLM router 未初始化，跳过 theme 细化（保持模板）")
        return theme

    genre = (genre_profile or {}).get("genre", "未知")
    characters_summary = ", ".join(
        str(c.get("name", "?")) for c in (key_characters or [])[:5]
    )

    system_prompt = (
        "你是网文题材策划，帮我设计本书的'共性主题 + 期待感弧'。"
        "我会给你：用户初始概念 + 题材画像 + 主角列表 + 模板 theme_spine。"
        "你需要："
        "1) theme_statement 改写得更具体（保留模板的共性维度，但加上本书特色）"
        "2) expectation_arc 重新算 3 个章节数字（必须 seed<twist<payoff）"
        "3) resonance_anchors 重写为 ≥3 条共性共鸣维度（不是具体剧情）"
        "约束：不含具体项目专名（角色名/地名/世界名），"
        "统一用'主角/配角/世界名'中性词。"
        "直接输出 JSON，结构: "
        '{"theme_statement": "...", "expectation_arc": {'
        '"seed_chapter": 1, "payoff_chapter": 80, "twist_chapter": 25, '
        '"description": "..."}, "resonance_anchors": ["...", "...", "..."]}'
    )
    user_prompt = (
        f"概念：{concept or '（未填）'}\n"
        f"题材：{genre}\n"
        f"主角：{characters_summary or '（未填）'}\n"
        f"模板：{theme}"
    )

    try:
        out, cost = router.call(
            agent_name="theme_designer",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=800,
            temperature=0.7,
        )
        refined = _parse_llm_json(out)
        if refined:
            # 字段兜底：LLM 缺字段时用模板字段补
            if not refined.get("theme_statement"):
                refined["theme_statement"] = theme["theme_statement"]
            if not refined.get("expectation_arc"):
                refined["expectation_arc"] = theme["expectation_arc"]
            else:
                # arc 内部字段兜底
                arc = refined["expectation_arc"]
                for k, default in theme["expectation_arc"].items():
                    arc.setdefault(k, default)
            if not refined.get("resonance_anchors") or len(refined["resonance_anchors"]) < 3:
                refined["resonance_anchors"] = theme["resonance_anchors"]
            refined["source"] = "llm"
            _log.info("theme_designer LLM 细化完成 cost=%.4f", cost)
            return refined
        else:
            _log.warning("theme_designer LLM 输出无法解析，保持模板")
            return theme
    except Exception as exc:
        _log.warning("theme_designer LLM 细化失败 err=%s — 保持模板", exc)
        return theme


def _parse_llm_json(text: str) -> dict | None:
    """解析 LLM 输出 JSON。"""
    try:
        from ..utils import parse_llm_json_response
        return parse_llm_json_response(text, default={})
    except Exception:
        return None