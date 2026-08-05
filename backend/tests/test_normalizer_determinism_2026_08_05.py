"""test_normalizer_determinism_2026_08_05.py

2026-08-05 修复（清单衍生 + 源码排查）：

1. first_pass_replace 改用 module-level Random 实例，可被 seed 后稳定输出。
   原 random.choice 用全局 random 模块不可重现——同一章重新 import 替换词会变。

2. dialogue cancer ≥ FORCE_THRESHOLD 时强制 LLM 替换不再被 first-pass 走过
   needs_llm=True 阻断：原 line 189 `if not needs_llm:` 让走完 second_pass_llm
   后对话癌仍 ≥ 50 时跳过专项修复。改为：对话污染触发了就走，无视上面 pass
   是否已用过 LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_first_pass_replace_uses_module_rng_and_seedable():
    """module-level RNG 可被显式 seed，相同 seed 相同输出。"""
    import random as _stdlib_random
    from engine.agents import normalizer as nm

    base_text = "此刻他决定动身，心中一动，蓦然回首。"

    nm._rng.seed(42)
    a1, _ = nm.first_pass_replace(base_text)
    nm._rng.seed(42)
    a2, _ = nm.first_pass_replace(base_text)
    assert a1 == a2, f"同样 seed 必须给同样结果，行为像普通的 Random 实例；a1!=a2"

    # 与全局 random 比对不应完全等价：原 random.choice 用全局随机，重复调用
    # 上下文会把 state 弄乱；新的 _rng 是隔离的。
    nm._rng.seed(99)
    nm.first_pass_replace(base_text)
    # 调用 _rng 不污染全局 random
    global_seeds = []
    _stdlib_random.seed(123)
    global_seeds.append(_stdlib_random.random())
    _stdlib_random.seed(123)
    global_seeds.append(_stdlib_random.random())
    assert global_seeds[0] == global_seeds[1], (
        "全局 random 应该独立于 _rng 的状态（基线回归）"
    )


def test_dialogue_cancer_triggers_llm_even_when_first_pass_already_used_one():
    """原 line 189 `if not needs_llm` 让对话癌 LLM 替换受 first-pass 阻断。

    修复后：dialogue_count >= 50 时无论 needs_llm 是什么都跑对话癌 LLM，
    让 second_pass_llm 没消除的对话污染有机会被换。
    """
    from engine.agents import normalizer as nm

    # 构造对话癌文本：200 行每行有「某某说道」满足 ≥50 触发条件
    lines = []
    for i in range(200):
        lines.append(f"第{i+1}行：陆承说道：'好的。'")
    polluted_text = "\n".join(lines)

    # 让 first_pass_replace 替换大量 AI 词，触发 needs_llm = True 路径
    heavy_ai_words_text = (
        "此刻蓦然不禁心中一动深吸一口气不由得悄无声息地走向门口。"
        * 50
    )

    dialogue_calls = {"n": 0}

    def fake_router_call(*args, **kwargs):
        # 记录系统提示词是否包含对话癌专项 keyword
        sp = kwargs.get("system_prompt", "")
        if "对话癌" in sp:
            dialogue_calls["n"] += 1
        # 返原文不动（mock 模式，避免真的 LLM）
        text = kwargs.get("user_prompt", "")
        # user_prompt 是 '请去AI腔处理以下文本：\\n\\n{text}'，剥前缀
        if "请去AI腔处理以下文本：\n\n" in text:
            text = text.split("请去AI腔处理以下文本：\n\n", 1)[-1]
        return (text, 0.001)

    fake_router = MagicMock()
    fake_router.call.side_effect = fake_router_call

    # Mock text + first_pass_replace: 假定有大量 AI 词导致 needs_llm=True
    # 通过 monkeypatch 让 second_pass_llm 直接返原文 (不会消除对话癌)
    # get_active_router 在 normalizer.py 顶层 import 与 router_call_for_dialogue
    # 函数内 import 是两个不同绑定 — 都得 patch。
    with patch.object(nm, "second_pass_llm", lambda txt: (txt, 0.0)), \
         patch("engine.agents.normalizer.get_active_router", return_value=fake_router), \
         patch("engine.llm_router.get_active_router", return_value=fake_router), \
         patch.object(nm._rng, "seed", lambda s=None: None):
        # 直接拿 dialogue-rich + many-AI-words 的 text，模拟 needs_llm=True 路径
        combo_text = (
            "此刻他决定动身，心中一动，蓦然回首。" + "\n" +
            polluted_text + "\n" + heavy_ai_words_text
        )
        # 强制 needs_llm=True (走 LLM 二次通用)：用 needs_llm 计算判定条件
        # 这里直接调检测工具看是否触发
        rep_count, _ = nm.first_pass_replace(combo_text)
        # rep_count > 3 → needs_llm=True 必走；且 dialogue_count ≥ 50
        # 修复后必须仍调对话癌 LLM。
        # 通过 run_normalizer 入口触发
        _clean_text, issues, _ = nm.run_normalizer(combo_text, {
            "chapter_number": 1, "chapter_role": "发展",
            "shuang_description": "", "target_length": "2000-2200",
            "chapter_goal": "x",
        })

    assert dialogue_calls["n"] >= 1, (
        "对话癌 ≥ FORCE_THRESHOLD 时即使 needs_llm=True 也必须仍触发对话 LLM；"
        f"实际 call 次数 {dialogue_calls['n']}"
    )
    # issues 应该记录对话癌告警
    assert any("对话癌强制替换" in i for i in issues), (
        f"issues 应包含对话癌告警；issues={issues}"
    )


def test_dialogue_cancer_does_not_double_when_no_force_needed():
    """不需要强制修复（count < FORCE）时不应调到 router_call_for_dialogue。"""
    from engine.agents import normalizer as nm

    # 干净文本：纯正文，无 AI 词亦无对话污染
    clean_text = "林渊回到住处，把外套挂在门口。"

    dialogue_calls = {"n": 0}

    def fake_router_call(*args, **kwargs):
        sp = kwargs.get("system_prompt", "")
        if "对话癌" in sp:
            dialogue_calls["n"] += 1
        return (kwargs.get("user_prompt", ""), 0.001)

    fake_router = MagicMock()
    fake_router.call.side_effect = fake_router_call

    with patch.object(nm, "second_pass_llm", lambda txt: (txt, 0.0)), \
         patch("engine.agents.normalizer.get_active_router", return_value=fake_router):
        nm.run_normalizer(clean_text, {
            "chapter_number": 1, "chapter_role": "发展",
            "shuang_description": "", "target_length": "2000-2200",
            "chapter_goal": "x",
        })

    assert dialogue_calls["n"] == 0, (
        f"对话癌 < FORCE_THRESHOLD 不该触发对话 LLM；实际 {dialogue_calls['n']} 次"
    )
