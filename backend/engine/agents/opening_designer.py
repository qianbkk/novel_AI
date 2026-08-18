"""opening_designer.py - v1.0 Stage C 黄金三章设计

输入：concept + theme_spine + genre_profile + key_characters
输出：opening_design (output/opening_design.json)

核心结构（docs/drafts/v1-quality-first-design.md § Stage 1c）：
- chapter_1_anchor: 锚定期望（场景 + hook + 情绪 + show-item + 期望播种）
- chapter_2_question: 建立问题（场景 + hook + 读者问题 + 期望推进）
- chapter_3_escalation: 升级/翻转（场景 + hook + 情绪 + 期望翻转）

CLAUDE.md 红线：
- 必填字段缺失 → InvalidOpeningError（不让半成品落盘）
- LLM 失败 → 保留模板 + log.warning
- 不含具体项目专名（角色名/地名/世界名）
- hook_type 必须是 7 个合法 hook 之一（HOOK_TYPES 校验）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config.prompt_templates import HOOK_TYPES
from ..llm_router import get_active_router
from ..utils import parse_llm_json_response


_log = logging.getLogger("novel_ai.engine.agents.opening_designer")


REQUIRED_OPENING_FIELDS = (
    "chapter_1_anchor",
    "chapter_2_question",
    "chapter_3_escalation",
    "source",
)

# 每章必填子字段（schema）
# 注意：ch1 必填 show_item_seed（首次播种具体物件），
#      ch2/ch3 必填 show_item_used（与 ch1 同物件接力，构成 show-item chain）。
#      这是 v1.0 § Stage H show_item_chain 的种子数据。
_CHAPTER_REQUIRED: dict[str, tuple[str, ...]] = {
    "chapter_1_anchor": (
        "scene", "hook_type", "reader_emotion_to_install",
        "show_item_seed", "expectation_seed",
    ),
    "chapter_2_question": (
        "scene", "hook_type", "reader_question",
        "show_item_used", "expectation_shift",
    ),
    "chapter_3_escalation": (
        "scene", "hook_type", "reader_emotion_to_install",
        "show_item_used", "expectation_shift",
    ),
}


# ── 异常 ─────────────────────────────────────────────

class InvalidOpeningError(ValueError):
    """opening_design 字段不全或结构错误。"""


# ── 6 个题材的黄金三章种子模板 ─────────────────────────
# 每个 genre 用不同的开场模式：
# - 玄幻: 觉醒开局（被低估 + 异象 + 觉醒）→ 试炼 → 大陆真相
# - 仙侠: 道心开局（悟道 + 师门 + 劫波）→ 问道 → 飞升诱惑
# - 都市: 翻盘开局（困局 + 机会 + 失而复得）→ 选择 → 试金石
# - 历史: 乱世开局（服徭役 + 归家 + 征召）→ 羁绊 → 方向翻转
# - 军事: 战地开局（初战 + 老兵 + 战友情）→ 牺牲 → 战局
# - 科幻: 宇宙开局（飞船 + 孤独 + 信号）→ 选择 → 真相

_OPENING_TEMPLATES: dict[str, dict] = {
    "xuanhuan": {
        "chapter_1_anchor": {
            "scene": {
                "where": "家族祠堂外的石阶",
                "who_present": ["主角", "族人", "长老"],
                "time": "清晨",
                "weather": "薄雾",
            },
            "hook_type": "信息钩",
            "reader_emotion_to_install": "期待 + 隐忧",
            "show_item_seed": "主角手指上未干透的血（来自昨夜试炼）",
            "expectation_seed": "主角的血脉即将觉醒，但没人看好他",
        },
        "chapter_2_question": {
            "scene": {
                "where": "试炼之地深处",
                "who_present": ["主角", "异兽"],
                "time": "午后",
                "weather": "山风",
            },
            "hook_type": "危机钩",
            "reader_question": "觉醒的代价，主角能不能承受？",
            "show_item_used": "主角手指上那未干透的血（这次带着异兽的气息）",
            "expectation_shift": "从'是否觉醒' → '觉醒后的代价是什么'",
        },
        "chapter_3_escalation": {
            "scene": {
                "where": "家族议事厅",
                "who_present": ["主角", "族长", "宿敌"],
                "time": "黄昏",
                "weather": "风起",
            },
            "hook_type": "反转钩",
            "reader_emotion_to_install": "震惊 + 重新评估",
            "show_item_used": "那根手指上的血沾上了族谱的某一页",
            "expectation_shift": "血脉来源被揭示：不是族谱所记的那一支",
        },
    },
    "xianxia": {
        "chapter_1_anchor": {
            "scene": {
                "where": "山门外的云海",
                "who_present": ["主角", "师弟", "师尊"],
                "time": "破晓",
                "weather": "云开",
            },
            "hook_type": "情感钩",
            "reader_emotion_to_install": "宁静 + 预感",
            "show_item_seed": "师尊给主角的一枚旧玉简（从未传功）",
            "expectation_seed": "主角即将下山历练，但师尊似乎有未说的事",
        },
        "chapter_2_question": {
            "scene": {
                "where": "下山路上",
                "who_present": ["主角", "魔修"],
                "time": "黄昏",
                "weather": "细雨",
            },
            "hook_type": "对抗钩",
            "reader_question": "主角的道心，是守住还是动摇？",
            "show_item_used": "那枚旧玉简（主角在打斗中握紧又松手）",
            "expectation_shift": "从'下山历练' → '道心在红尘中被试炼'",
        },
        "chapter_3_escalation": {
            "scene": {
                "where": "凡间小镇",
                "who_present": ["主角", "凡人"],
                "time": "深夜",
                "weather": "雾",
            },
            "hook_type": "反转钩",
            "reader_emotion_to_install": "震撼",
            "show_item_used": "玉简在主角掌心发热（师尊真的在看着）",
            "expectation_shift": "主角的'道'被凡人一句话击中：修仙修的到底是什么",
        },
    },
    "dushi": {
        "chapter_1_anchor": {
            "scene": {
                "where": "主角租住的城中村小单间",
                "who_present": ["主角", "房东"],
                "time": "深夜",
                "weather": "闷热",
            },
            "hook_type": "悬念钩",
            "reader_emotion_to_install": "压抑 + 隐忍",
            "show_item_seed": "主角桌上那张揉皱又被抚平的离职信",
            "expectation_seed": "主角要离开困局，但不知道去哪",
        },
        "chapter_2_question": {
            "scene": {
                "where": "前公司楼下的咖啡店",
                "who_present": ["主角", "旧同事"],
                "time": "午后",
                "weather": "晴",
            },
            "hook_type": "对抗钩",
            "reader_question": "主角要不要接住这次机会？",
            "show_item_used": "那封信被主角揣进口袋又拿出",
            "expectation_shift": "从'离开困局' → '离开后的下一步是不是真的更好'",
        },
        "chapter_3_escalation": {
            "scene": {
                "where": "新公司的会议室",
                "who_present": ["主角", "新上司"],
                "time": "傍晚",
                "weather": "晴",
            },
            "hook_type": "反转钩",
            "reader_emotion_to_install": "重新评估",
            "show_item_used": "信被主角叠成小方块压在桌下",
            "expectation_shift": "主角发现新机会的本质：不是逃离，是另一种困局",
        },
    },
    "lishi": {
        "chapter_1_anchor": {
            "scene": {
                "where": "服徭役的工地外",
                "who_present": ["主角", "邻役", "监工"],
                "time": "黄昏",
                "weather": "风起",
            },
            "hook_type": "悬念钩",
            "reader_emotion_to_install": "期待 + 隐忧",
            "show_item_seed": "主角行囊底层那双母亲做的、三年没舍得穿的新布鞋",
            "expectation_seed": "主角服徭役期满，明天就能回家",
        },
        "chapter_2_question": {
            "scene": {
                "where": "归乡路上的驿站",
                "who_present": ["主角", "邻家少年"],
                "time": "深夜",
                "weather": "细雨",
            },
            "hook_type": "对抗钩",
            "reader_question": "主角能不能带邻家少年一起逃？",
            "show_item_used": "那双布鞋被主角解下来让邻家少年穿上",
            "expectation_shift": "从'主角一个人回家' → '归途多了一个不能丢下的人'",
        },
        "chapter_3_escalation": {
            "scene": {
                "where": "县城征兵处门外",
                "who_present": ["主角", "征兵官", "邻家少年"],
                "time": "破晓",
                "weather": "风紧",
            },
            "hook_type": "反转钩",
            "reader_emotion_to_install": "矛盾（好事变坏事？）",
            "show_item_used": "那双布鞋被征兵官踏了一脚",
            "expectation_shift": "征召方向 = 家的方向；'回家'本身变成了谜团",
        },
    },
    "junshi": {
        "chapter_1_anchor": {
            "scene": {
                "where": "前线战壕",
                "who_present": ["主角", "班长"],
                "time": "黎明",
                "weather": "雾",
            },
            "hook_type": "情感钩",
            "reader_emotion_to_install": "紧张 + 信任",
            "show_item_seed": "班长给主角的水壶，盖子拧得比平时紧了一圈",
            "expectation_seed": "主角第一次实战，班长会护住他",
        },
        "chapter_2_question": {
            "scene": {
                "where": "战壕后方的临时掩体",
                "who_present": ["主角", "老兵"],
                "time": "正午",
                "weather": "晴",
            },
            "hook_type": "对抗钩",
            "reader_question": "主角要不要把侦察到的情报上报？",
            "show_item_used": "那个水壶（盖子被老兵从主角手里接过去）",
            "expectation_shift": "从'主角成长' → '成长要付出什么代价'",
        },
        "chapter_3_escalation": {
            "scene": {
                "where": "战友牺牲的阵地",
                "who_present": ["主角", "战友遗物"],
                "time": "黄昏",
                "weather": "风起",
            },
            "hook_type": "反转钩",
            "reader_emotion_to_install": "悲恸 + 重新定义'战士'",
            "show_item_used": "战友留给主角的水壶（盖子还是紧的）",
            "expectation_shift": "主角从'立功' 转向'替战友活下去'",
        },
    },
    "kehuan": {
        "chapter_1_anchor": {
            "scene": {
                "where": "飞船生态舱",
                "who_present": ["主角", "飞船 AI"],
                "time": "人类时间 03:14",
                "weather": "无重力",
            },
            "hook_type": "悬念钩",
            "reader_emotion_to_install": "孤独 + 警觉",
            "show_item_seed": "主角每天浇水的一盆植物（飞船已离开母星 12 年）",
            "expectation_seed": "飞船收到来自未知方向的信号",
        },
        "chapter_2_question": {
            "scene": {
                "where": "飞船控制台",
                "who_present": ["主角"],
                "time": "人类时间 09:27",
                "weather": "无重力",
            },
            "hook_type": "对抗钩",
            "reader_question": "主角该不该回应这个信号？",
            "show_item_used": "那盆植物被主角挪到控制台边（叶尖朝向信号方向）",
            "expectation_shift": "从'回应信号' → '回应意味着什么不可逆'",
        },
        "chapter_3_escalation": {
            "scene": {
                "where": "飞船货舱",
                "who_present": ["主角", "信号源"],
                "time": "人类时间 22:00",
                "weather": "无重力",
            },
            "hook_type": "反转钩",
            "reader_emotion_to_install": "重新理解人类本身",
            "show_item_used": "植物在信号源前开出第一朵花",
            "expectation_shift": "信号源是地球的——但时间戳是 200 年前",
        },
    },
}


# ── Public API ─────────────────────────────────────────────

def design_opening(
    concept: str,
    theme_spine: dict,
    genre_profile: dict,
    key_characters: list[dict],
    *,
    use_llm: bool = False,
    novel_id: str | None = None,
) -> dict:
    """为指定 project 设计黄金三章 opening_design。

    Args:
        concept: 用户初始概念（一句话）
        theme_spine: 来自 theme_designer 的 spine dict
        genre_profile: 来自 genre_profiler 的 profile dict
        key_characters: 主角列表
        use_llm: 是否用 LLM 在模板基础上改写（CI 友好默认 False）
        novel_id: 项目 ID（落盘用）

    Returns:
        opening_design dict，含 REQUIRED_OPENING_FIELDS 全部 4 个字段
    """
    template = _pick_template(genre_profile)

    opening = _template_to_opening(template, source="template")

    if use_llm:
        opening = _refine_with_llm(opening, concept, theme_spine, genre_profile, key_characters)

    if novel_id:
        save_opening(novel_id, opening)

    return opening


def save_opening(novel_id: str, opening: dict) -> None:
    """落盘 opening_design（用户编辑或 AI 生成都走这条路径）。

    Args:
        novel_id: 项目 ID
        opening: 完整 opening dict

    Raises:
        InvalidOpeningError: 缺字段 / 类型错 / hook_type 不合法
    """
    if not isinstance(opening, dict):
        raise InvalidOpeningError(f"opening_design 必须是 dict，实际 {type(opening).__name__}")

    for f in REQUIRED_OPENING_FIELDS:
        if f not in opening:
            raise InvalidOpeningError(
                f"opening_design 缺字段 {f!r}；落盘前必须含 {REQUIRED_OPENING_FIELDS}"
            )

    valid_hooks = set(HOOK_TYPES.keys())

    for ch_name, required_subs in _CHAPTER_REQUIRED.items():
        ch = opening.get(ch_name)
        if not isinstance(ch, dict):
            raise InvalidOpeningError(
                f"{ch_name} 必须是 dict，实际 {type(ch).__name__}"
            )
        for sub in required_subs:
            if sub not in ch:
                raise InvalidOpeningError(
                    f"{ch_name} 缺子字段 {sub!r}；必填 {required_subs}"
                )
        # hook_type 必须是 7 个合法之一
        ht = ch.get("hook_type")
        if ht not in valid_hooks:
            raise InvalidOpeningError(
                f"{ch_name}.hook_type {ht!r} 不在合法 hook 内 {sorted(valid_hooks)}"
            )
        # scene.where / scene.who_present 必填
        scene = ch["scene"]
        if not isinstance(scene, dict) or "where" not in scene or "who_present" not in scene:
            raise InvalidOpeningError(
                f"{ch_name}.scene 必须是含 where + who_present 的 dict"
            )
        if not isinstance(scene["who_present"], list):
            raise InvalidOpeningError(
                f"{ch_name}.scene.who_present 必须是 list，实际 {type(scene['who_present']).__name__}"
            )

    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "opening_design.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    target.write_text(_json.dumps(opening, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("opening_design 落盘: %s (source=%s)", target, opening.get("source"))


def load_opening(novel_id: str) -> dict | None:
    """加载已落盘的 opening_design（启动时检测，老项目 bootstrap 自动按 default 初始化）。

    2026-08-18 修复（CLAUDE.md「失败要响亮」）：见 genre_profiler.load_profile 注释。
    """
    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "opening_design.json"
    if not target.is_file():
        return None
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        for f_name in REQUIRED_OPENING_FIELDS:
            if f_name not in data:
                _log.warning("opening_design 缺字段 %s，视为损坏: %s", f_name, target)
                return None
        return data
    except Exception:
        _log.exception("load_opening 读取失败（将视作无 opening）: %s", target)
        return None


# ── Internal helpers ─────────────────────────────────────────────

def _pick_template(genre_profile: dict) -> dict:
    """按 genre_profile.genre_key 选种子模板。"""
    key = (genre_profile or {}).get("genre_key") or ""
    return _OPENING_TEMPLATES.get(key, _OPENING_TEMPLATES["lishi"])  # 通用 fallback


def _template_to_opening(template: dict, *, source: str) -> dict:
    """模板 → opening dict（含 source 标注）。"""
    return {
        "chapter_1_anchor": _deep_copy_chapter(template["chapter_1_anchor"]),
        "chapter_2_question": _deep_copy_chapter(template["chapter_2_question"]),
        "chapter_3_escalation": _deep_copy_chapter(template["chapter_3_escalation"]),
        "source": source,
    }


def _deep_copy_chapter(ch: dict) -> dict:
    """深拷贝 chapter dict（避免上层修改污染模板）。"""
    import copy
    return copy.deepcopy(ch)


def _refine_with_llm(
    opening: dict,
    concept: str,
    theme_spine: dict,
    genre_profile: dict,
    key_characters: list[dict],
) -> dict:
    """用 LLM 在模板基础上改写 opening_design（不破坏结构性字段）。

    LLM 失败 → 保留模板 + log.warning。
    LLM 字段缺失 → 用模板字段兜底。
    """
    router = get_active_router()
    if router is None:
        _log.warning("LLM router 未初始化，跳过 opening 细化（保持模板）")
        return opening

    genre = (genre_profile or {}).get("genre", "未知")
    theme_stmt = (theme_spine or {}).get("theme_statement", "")
    characters_summary = ", ".join(
        str(c.get("name", "?")) for c in (key_characters or [])[:5]
    )

    system_prompt = (
        "你是网文题材策划，帮我设计黄金三章（前 3 章开场）。"
        "我会给你：用户初始概念 + 题材画像 + 共性主题 + 模板黄金三章。"
        "你需要："
        "1) chapter_1_anchor 改写：场景具体化（保留模板共性方向）"
        "2) chapter_2_question 改写：reader_question 更具体"
        "3) chapter_3_escalation 改写：expectation_shift 更具体"
        "约束："
        "- hook_type 必须是以下 7 个之一: " + ", ".join(HOOK_TYPES.keys()) + ";"
        "  错误填法会让下游渲染乱套"
        "- scene 必含 where + who_present"
        "- 不含具体项目专名（角色名/地名/世界名），统一用'主角/配角/世界名'中性词"
        "直接输出 JSON，结构: "
        '{"chapter_1_anchor": {scene: {where, who_present}, hook_type, '
        'reader_emotion_to_install, show_item_seed, expectation_seed}, '
        '"chapter_2_question": {scene: {where, who_present}, hook_type, '
        'reader_question, expectation_shift}, '
        '"chapter_3_escalation": {scene: {where, who_present}, hook_type, '
        'reader_emotion_to_install, expectation_shift}}'
    )
    user_prompt = (
        f"概念：{concept or '（未填）'}\n"
        f"题材：{genre}\n"
        f"共性主题：{theme_stmt}\n"
        f"主角：{characters_summary or '（未填）'}\n"
        f"模板：{opening}"
    )

    try:
        out, cost = router.call(
            agent_name="opening_designer",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1200,
            temperature=0.7,
        )
        refined = parse_llm_json_response(out, default={})
        if refined:
            # 字段兜底
            for ch_name in _CHAPTER_REQUIRED:
                if not refined.get(ch_name):
                    refined[ch_name] = opening[ch_name]
                else:
                    for sub, default in opening[ch_name].items():
                        refined[ch_name].setdefault(sub, default)
            refined["source"] = "llm"
            _log.info("opening_designer LLM 细化完成 cost=%.4f", cost)
            return refined
        else:
            _log.warning("opening_designer LLM 输出无法解析，保持模板")
            return opening
    except Exception as exc:
        _log.warning("opening_designer LLM 细化失败 err=%s — 保持模板", exc)
        return opening


# 2026-08-18 修复（CLAUDE.md「失败要响亮」）：删除 _parse_llm_json wrapper。
# utils.parse_llm_json_response 本身已 log + 处理失败；wrapper 反而吞了 import error。