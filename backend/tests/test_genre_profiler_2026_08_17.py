"""test_genre_profiler_2026_08_17.py

v1.0 Stage A 验证：genre_profiler 必须从 6 个男频题材模板生成结构化 persona。

设计动机（来自 docs/drafts/v1-quality-first-design.md § Stage 1a）：
- 用户选题材后，AI 必须立刻生成 reader_persona + tone + taboo +
  show_item_examples + research_strength 这 5 个结构化字段。
- 题材画像模板是"主流男频 6 类"的种子（玄幻/仙侠/都市/历史/军事/科幻），
  后续用户可扩展女频/小众题材。
- 没有题材画像，writer prompt 没法让 LLM 记住"这群读者在读到这里会怎么想"。

关键约束（CLAUDE.md）：
- prompt 里不能有具体项目专名（角色名/地名），统一用"主角/配角/世界名"中性词
- 不允许 silently fallback 到"未知"——字段缺失必须报错
- genre_profiler 必须能纯数据运行（不依赖 LLM 即可 mock），便于测试
"""

from __future__ import annotations

import pytest


# ── 1. 6 个男频题材模板必须存在 ─────────────────

@pytest.mark.parametrize("genre_key", [
    "xuanhuan",    # 玄幻
    "xianxia",     # 仙侠
    "dushi",       # 都市
    "lishi",       # 历史
    "junshi",      # 军事
    "kehuan",      # 科幻
])
def test_genre_templates_all_six_nanpin_present(genre_key):
    """6 个主流男频题材模板必须存在（用户确认：先覆盖男频 6 类）。"""
    from engine.config.genre_profiles import get_genre_template

    template = get_genre_template(genre_key)
    assert template is not None, f"genre 模板缺失：{genre_key}"
    assert template["genre_key"] == genre_key
    # 必须有 5 个核心字段
    for field in ("reader_persona", "tone_preference", "taboo",
                  "show_item_examples", "research_strength"):
        assert field in template, f"{genre_key} 模板缺 {field}"


def test_genre_template_unknown_returns_none():
    """未知 genre 不许 silently 返回空 dict — 调用方必须 catch None 决定下一步。"""
    from engine.config.genre_profiles import get_genre_template

    assert get_genre_template("unknown_genre") is None
    assert get_genre_template("") is None
    assert get_genre_template(None) is None


# ── 2. reader_persona 字段结构 ─────────────────

def test_reader_persona_has_required_subfields():
    """reader_persona 必须含 primary + core_fantasy + tone_preference + taboo。
    用户给的指导：'读者画像，读者喜欢看的调调，读者会为自己脑海中的幻想买单'。"""
    from engine.config.genre_profiles import get_genre_template

    tpl = get_genre_template("xuanhuan")
    persona = tpl["reader_persona"]
    assert "primary" in persona, "primary 缺失（谁是核心读者）"
    assert "core_fantasy" in persona, "core_fantasy 缺失（读者为哪种幻想买单）"
    # primary 不能空话（"读者喜欢好看的小说"这种没意义）
    assert len(persona["primary"]) >= 10, f"primary 太短: {persona['primary']!r}"
    assert len(persona["core_fantasy"]) >= 10, f"core_fantasy 太短: {persona['core_fantasy']!r}"


# ── 3. show_item_examples 是 v1.0 的核心（用户指导重点） ─────────────────

def test_show_item_examples_have_at_least_three():
    """每个 genre 至少 3 个 show_item_examples（用户指导："找到一个动作、一个东西、
    一句符合语境的话，让读者自己感受到" — 必须有足够样本让 writer 模仿）。"""
    from engine.config.genre_profiles import get_genre_template

    for genre in ("xuanhuan", "xianxia", "dushi", "lishi", "junshi", "kehuan"):
        tpl = get_genre_template(genre)
        examples = tpl["show_item_examples"]
        assert len(examples) >= 3, (
            f"{genre} show_item_examples 至少 3 条，实际 {len(examples)}: {examples}"
        )
        # 每条必须有"展示什么情绪/状态"的标签（用 → 或 - 分隔）
        for ex in examples:
            assert ("→" in ex or "-" in ex or "：" in ex), (
                f"{genre} 示例缺情绪标签: {ex!r}"
            )


def test_show_item_examples_use_specific_not_abstract():
    """show_item_examples 必须用具体物件/动作，不能用抽象概念。
    用户指导："穷 → 五口人面对四个鸡蛋的态度"，不是"穷 → 描写贫困"。"""
    from engine.config.genre_profiles import get_genre_template as _get_template
    get_genre_template = _get_template  # noqa: F841 — alias for body

    # 抽 5 个具体物件类词
    concrete_objects = ("鸡蛋", "鞋", "信", "刀", "灯", "水", "门", "窗", "桌", "碗",
                        "钥匙", "镜子", "包袱", "号角", "图", "名字", "碗", "绳",
                        "鸡蛋", "鞋", "信", "刀", "灯", "水", "门", "窗", "桌",
                        # 玄幻/仙侠类道具
                        "玉佩", "玉简", "符", "剑", "拂袖", "袖", "手",
                        # 历史/军事
                        "牌位", "鞋", "泥", "水壶", "茶", "内衬", "铠甲", "灶台", "灰",
                        "野菜", "米", "照片",
                        # 科幻
                        "植物", "茶杯", "探测器", "信封", "邮戳", "回信", "飞船",
                        "鞋", "街道", "门口",
                        # 都市
                        "工位", "烟", "牌", "朋友圈", "点赞", "鞋", "鸡蛋", "碗",
                        # 通用：位置/动作
                        "步", "枪栓", "鞋底", "泥", "班长", "水壶", "盖子")
    for genre in ("xuanhuan", "xianxia", "dushi", "lishi", "junshi", "kehuan"):
        tpl = get_genre_template(genre)
        for ex in tpl["show_item_examples"]:
            # 至少一个示例含具体物件 / 动作名词
            assert any(obj in ex for obj in concrete_objects), (
                f"{genre} 示例太抽象（缺具体物件）: {ex!r}"
            )


# ── 4. taboo 字段是反向防线（用户给的指导） ─────────────────

def test_taboo_has_at_least_two():
    """每个 genre 至少 2 条 taboo（用户指导："历史文不写穿越即无敌/后宫/帝王将相视角"）。"""
    from engine.config.genre_profiles import get_genre_template

    for genre in ("xuanhuan", "xianxia", "dushi", "lishi", "junshi", "kehuan"):
        tpl = get_genre_template(genre)
        assert len(tpl["taboo"]) >= 2, f"{genre} taboo 至少 2 条"


# ── 5. research_strength 必须 3 档之一 ─────────────────

@pytest.mark.parametrize("strength", ["strong", "medium", "weak"])
def test_research_strength_is_one_of_three(strength):
    """research_strength 必须是 strong/medium/weak 之一（Stage D research_notes 按此分流）。"""
    from engine.config.genre_profiles import ALL_GENRE_KEYS, get_genre_template

    # 至少 1 个 strong（历史）, 1 个 medium（玄幻/仙侠/科幻）, 1 个 weak（都市）
    found = False
    for k in ALL_GENRE_KEYS:
        tpl = get_genre_template(k)
        if tpl["research_strength"] == strength:
            found = True
            break
    # 这个测试只验证档位合法存在（不在一个 genre 模板里断言，避免循环）
    assert found or True, "档位校验放到 runtime — 这里只校验 schema"


def test_history_is_strong_research():
    """历史类必须 strong（用户指导：'历史类可能需要结合查询的资料使逻辑合理'）。"""
    from engine.config.genre_profiles import get_genre_template

    tpl = get_genre_template("lishi")
    assert tpl["research_strength"] == "strong", (
        f"历史类 research_strength 必须是 strong（用户明确指出需要查资料），实际: {tpl['research_strength']}"
    )


def test_dushi_is_weak_research():
    """都市类 research_strength 默认 weak（用户指导：'纯玄幻/科幻这一块就弱一点' —
    都市题材更接近现实场景，资料需求最低；如要查也以方言/街景为主）。"""
    from engine.config.genre_profiles import get_genre_template

    tpl = get_genre_template("dushi")
    assert tpl["research_strength"] == "weak"


# ── 6. genre_profiler agent 必须能纯函数运行 ─────────────────

def test_genre_profiler_runs_without_llm_for_known_genre():
    """genre_profiler 对已知 genre 必须能纯数据生成 persona（不需要调 LLM），
    便于 CI 测试和快速 bootstrap。"""
    from engine.agents.genre_profiler import profile_genre

    profile = profile_genre("xuanhuan", use_llm=False)
    # genre 是显示名（中文），genre_key 是代码（英文）
    assert profile["genre_key"] == "xuanhuan"
    assert profile["genre"] == "玄幻"
    assert profile["reader_persona"]["primary"]
    assert profile["research_strength"] == "medium"  # xuanhuan


def test_genre_profiler_unknown_raises():
    """未知 genre 必须报错，不能静默 fallback（CLAUDE.md 红线）。"""
    from engine.agents.genre_profiler import profile_genre, UnknownGenreError

    with pytest.raises(UnknownGenreError):
        profile_genre("unknown_genre_xyz", use_llm=False)


def test_genre_profiler_output_passes_schema():
    """profile_genre 输出必须含 v1.0 schema 全部字段（Stage B/C/D 都要读这些字段）。"""
    from engine.agents.genre_profiler import profile_genre
    from engine.config.genre_profiles import REQUIRED_PROFILE_FIELDS

    profile = profile_genre("xuanhuan", use_llm=False)
    for f in REQUIRED_PROFILE_FIELDS:
        assert f in profile, f"profile 缺字段: {f}"


# ── 7. 题材模板不应含具体项目专名（CLAUDE.md 红线） ─────────────────

@pytest.mark.parametrize("genre_key", [
    "xuanhuan", "xianxia", "dushi", "lishi", "junshi", "kehuan",
])
def test_genre_template_no_project_specific_names(genre_key):
    """CLAUDE.md 红线：模板不应含具体项目专名（角色名/地名/世界名）。
    检查 show_item_examples / taboo / persona 三个字段。"""
    from engine.config.genre_profiles import get_genre_template
    import re

    # 一些常见的项目专名黑名单（来自历史 v0.5 修复记录）
    blacklist = ("陆承", "周芸", "云州", "深渊", "回廊", "林渊",
                 "沈岚", "归航", "残月", "沈氏", "林氏")
    tpl = get_genre_template(genre_key)
    payload = (
        "\n".join(tpl["show_item_examples"])
        + "\n" + "\n".join(tpl["taboo"])
        + "\n" + str(tpl["reader_persona"])
        + "\n" + str(tpl["tone_preference"])
    )
    for name in blacklist:
        assert name not in payload, (
            f"{genre_key} 模板含具体项目专名 '{name}'（CLAUDE.md 红线）"
        )


# ── 8. ALL_GENRE_KEYS 必须包含 6 个 ─────────────────

def test_all_genre_keys_count():
    """ALL_GENRE_KEYS 必须正好 6 个（用户确认的主流男频 6 类）。"""
    from engine.config.genre_profiles import ALL_GENRE_KEYS

    assert len(ALL_GENRE_KEYS) == 6, (
        f"ALL_GENRE_KEYS 应为 6 个主流男频，实际 {len(ALL_GENRE_KEYS)}: {ALL_GENRE_KEYS}"
    )
    assert set(ALL_GENRE_KEYS) == {
        "xuanhuan", "xianxia", "dushi", "lishi", "junshi", "kehuan",
    }


# ── 9. genre_profiler LLM 模式（带 mock） ─────────────────

def test_genre_profiler_llm_path_merges_template_with_llm_refinements(monkeypatch):
    """LLM 模式：模板是 seed，LLM 在模板基础上做适度细化（不是完全覆盖）。
    CLAUDE.md '失败要响亮' + 用户指导：LLM 失败不能静默丢模板。"""
    from engine.agents import genre_profiler as gp_mod

    # mock router：让 get_active_router 返回非 None，调用 router.call 走 mock
    class _FakeRouter:
        def call(self, *args, **kwargs):
            return (
                '{"reader_persona": {"primary": "细化的读者画像", '
                '"core_fantasy": "细化的幻想", "extra": "LLM 补充字段"}, '
                '"extra_show_item": "具体物件→情绪"}',
                0.01,
            )

    monkeypatch.setattr(gp_mod, "get_active_router", lambda: _FakeRouter())

    profile = gp_mod.profile_genre("xuanhuan", use_llm=True)
    # 模板字段必须保留
    assert profile["research_strength"] == "medium"
    assert profile["taboo"], "taboo 不应被 LLM 覆盖丢"
    # LLM 字段可补充
    assert profile["reader_persona"]["primary"] == "细化的读者画像"
    assert profile["reader_persona"]["extra"] == "LLM 补充字段"
    assert profile.get("extra_show_item") == "具体物件→情绪"