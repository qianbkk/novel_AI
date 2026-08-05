"""Normalizer Agent V2 — 去AI腔处理
三道防线 + 集成文风指纹统计检测

Migrated from novel_AI/agents/normalizer_agent.py. Uses backend.engine.llm.router
(via get_active_router() from engine.llm_router) instead of api_client.call_llm.
"""
from __future__ import annotations
import re
import random

from ..llm.router import LLMRouter
from ..llm_router import get_active_router

AI_WORDS = {
    "此刻":["这时候","这会儿","此时"], "蓦然":["突然","忽然","猛地"],
    "不禁":["忍不住","没忍住",""], "心中一动":["愣了一下","心里一跳",""],
    "深吸一口气":["停顿了一下","沉默片刻",""], "不由得":["忍不住","没忍住",""],
    "一丝":["一点","些许",""], "莫名":["说不清","不知道为什么",""],
    "涌上心头":["冒出来","浮现",""], "眼眸":["眼睛","目光",""],
    "嘴角微扬":["扯了扯嘴角","笑了一下",""], "眸子":["眼睛",""],
    "沉声":["开口","说",""], "缓缓":["慢慢",""], "悄然":["悄悄",""],
    "骤然":["突然","猛地",""], "凝视":["盯着","看着",""],
    "喃喃":["小声说","低声道",""], "霎时":["瞬间","一下子",""],
    "不料":["没想到",""], "倏地":["猛地","忽然",""],
    "话音刚落":["话刚说完","话音未落","刚说完"],
    "此话一出":["这话一出","这句话说出来",""],
    "正因如此":["所以","因此",""], "话虽如此":["虽然这么说",""],
    "不得不承认":["说实话","老实说",""], "归根结底":["说到底",""],
}

NORMALIZER_SYSTEM = """你是网文编辑，专门处理AI生成文本的表达痕迹。
改写规则：
1. 情节100%不变，只改表达方式
2. 消除对称排比句式（「既...又...」「一方面...另一方面...」）
3. 压缩连续感叹句（两个以上感叹号的段落，精简为一个）
4. 心理描写改为行动/对话展示（「他感到很高兴」→ 写出让人看出高兴的行为）
5. 保留【系统提示】格式不变
直接输出改写后正文，不加任何说明。"""

AI_PATTERNS = ["既然如此","话虽如此","不得不承认","归根结底","正因如此",
               "话音刚落","此话一出","不由得","莫名其妙地"]


# ═══════════════════════════════════════════════
# 对话癌检测（2026-07-25 战略审视 Commit 5）
# 来源：write/《写网文如何避免说和道》§4 种高级对话写法
# 阈值：每章 ≥25 次预警 + ≥50 次强制替换（原报告 5 次阈值错，详见 M5 备注）
# ═══════════════════════════════════════════════
DIALOGUE_TAGS_PATTERN = re.compile(
    r'([^\s，。！？,;.!?\n]{1,8})'  # 1-8 字说话人(非标点)
    r'(说|道|问道|答道|问道|问道|回答说|沉声道|低声说|冷冷地说|开口说|语气平静地说|语气平静的说)'
    r'([\s，：:,.:：""「」]|$)',  # 后接标点/引号/句末
)
# 4 种替换策略示例(只用于生成建议,真正替换在 LLM 二遍通中执行)
DIALOGUE_REPLACE_HINTS = {
    "动作卡位": "林栋跨出一步,脚下青石崩碎——'你敢动我妹!'",
    "神态神韵": "他嘴角勾起残忍弧度,目光锁死对方——'这次你跑不掉了'",
    "情境穿插": "雨水冲刷眼眶,远处火车长鸣——'你真要走?'",
    "语感辨识": "依靠角色专属口癖/口头禅(八戒'俺老猪',不需提示词)",
}
DIALOGUE_WARNING_THRESHOLD = 25   # ≥ 25 触发预警
DIALOGUE_FORCE_THRESHOLD = 50     # ≥ 50 触发强制替换


def detect_dialogue_pollution(text: str) -> tuple[int, list[str]]:
    """检测文本中的"对话提示词污染"(满篇"某某说""某某道")。

    Returns:
        (count, matches): count 是污染次数,matches 是具体匹配列表
        (含说话人+提示词+后接标点的完整片段)
    """
    matches = DIALOGUE_TAGS_PATTERN.findall(text)
    # findall 返回 tuple 列表,只保留第 1(说话人)+第 2(提示词)组成的字符串
    samples = [f"{s}{t}" for s, t, _ in matches if s and t]
    return len(matches), samples


def replace_dialogue_pollution(text: str, threshold: int = DIALOGUE_FORCE_THRESHOLD) -> str:
    """对话提示词超过阈值时,触发强制 LLM 替换(对话癌专项)。

    当前只做"打标 + 报告 + 标记污染段落",不直接修改(LLM 改写依赖上下文)。
    替换由 normalizer 主流程的 second_pass_llm 在收到"有对话癌"信号后触发。

    Returns:
        text: 原文本不变(标记已写进 metadata,通过 issues 返回)
    """
    count, samples = detect_dialogue_pollution(text)
    if count < threshold:
        return text
    # 不直接改 text(语义风险高);在 issue 里报信号,由 orchestrator 决定是否触发 LLM 替换
    return text


# ═══════════════════════════════════════════════
# POV 视角切换密度检测（2026-07-25 战略审视 Commit 6）
# 来源：write/《写小说的10个大坑》§8 视角混乱
# 阈值：每章 POV 切换次数 ≤ 2（多视角章节可放宽到 ≤ 3，需 task.pov_multi=true）
# ═══════════════════════════════════════════════
POV_SWITCH_MARKER = re.compile(r'【\s*POV\s*切换\s*[→\->]*\s*([^\]】]+)\s*】')
POV_SWITCH_WARNING_THRESHOLD = 2  # 默认章节 ≤ 2 次切换
POV_SWITCH_MULTI_THRESHOLD = 3    # 多视角章节（pov_multi=True）≤ 3 次


def detect_pov_switching(text: str) -> tuple[int, list[str]]:
    """检测文本中的 POV 视角切换标记。

    切换必须用 `【POV 切换 → {角色名}】` 标注;每次切换计 1 次。
    单章默认 ≤ 2 次(多视角章节 ≤ 3 次需 task.pov_multi=True)。

    Returns:
        (count, switches): 切换次数 + 切换目标角色列表
    """
    switches = [m.strip() for m in POV_SWITCH_MARKER.findall(text) if m.strip()]
    return len(switches), switches


def first_pass_replace(text: str) -> tuple[str, int]:
    count = 0
    for bad, goods in AI_WORDS.items():
        if bad in text:
            good = _rng.choice([g for g in goods if g] or [""])
            text = text.replace(bad, good)
            count += 1
    return text, count


# 2026-08-05 修复（清单衍生）：原 random.choice 用全局 random，重复跑 normalizer
# 输出不可重现。用 module-level Random 实例替代 — 序列基于定种子确定，
# 可被测试 / 调试场景 seed 后稳定输出。生产场景种子默认 None（行为==旧默认）。
_rng = random.Random()


def second_pass_llm(text: str) -> tuple[str, float]:
    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    out, cost = router.call(
        agent_name="normalizer",
        system_prompt=NORMALIZER_SYSTEM,
        user_prompt=f"请去AI腔处理以下文本：\n\n{text}",
        max_tokens=len(text) * 2 + 300,
        temperature=0.5,
    )
    return out, cost


def third_pass_format(text: str, task: dict) -> tuple[str, list]:
    issues = []
    paras = [p.strip() for p in text.split('\n') if p.strip()]
    long_p = [i+1 for i, p in enumerate(paras) if len(p) > 150 and not p.startswith('【')]
    if long_p:
        issues.append(f"段落过长(>150字)：第{long_p[:3]}段")
    dl = sum(len(p) for p in paras if p.startswith(('"', '「', '"')) or '说' in p[:5] or p[-1:] in ('」', '"'))
    total = sum(len(p) for p in paras)
    if total and dl/total < 0.25:
        issues.append(f"对话比例偏低({dl/total:.0%})")
    target = str(task.get("target_length", "2000-2200"))
    lo, hi = (map(int, target.split("-")) if "-" in target else (int(target)-200, int(target)+200))
    actual = sum(len(p) for p in paras)
    if actual < lo:
        issues.append(f"字数不足({actual}<{lo})")
    elif actual > hi + 500:
        issues.append(f"字数超标({actual}>{hi+500})")
    return text, issues


def run_normalizer(raw_text: str, task: dict) -> tuple[str, list, float]:
    """三道防线：字典替换 → LLM 改写 → 格式校验。
    + 2026-07-25 Commit 5：对话癌专项检测/替换（用 DIALOGUE_WARNING_THRESHOLD/FORCE_THRESHOLD）。

    Returns: (cleaned_text, issues_list, total_cost)
    """
    total_cost = 0.0
    text, replace_count = first_pass_replace(raw_text)
    needs_llm = replace_count > 3 or any(p in text for p in AI_PATTERNS)
    if needs_llm:
        text, cost = second_pass_llm(text)
        total_cost += cost
    text, issues = third_pass_format(text, task)

    # 2026-07-25 Commit 5：对话癌检测（独立于 AI 腔）
    dialogue_count, dialogue_samples = detect_dialogue_pollution(text)
    if dialogue_count >= DIALOGUE_FORCE_THRESHOLD:
        issues.append(
            f"对话癌强制替换(dialogue_pollution={dialogue_count}≥{DIALOGUE_FORCE_THRESHOLD}):"
            f"满篇'某某说/道',需走 LLM 二遍通用 4 种替换规则(动作卡位/神态/情境/语感)"
        )
        # 2026-08-05 修复（清单衍生）：原 `if not needs_llm:` 守门逻辑
        # 会让 first_pass 已经走过 LLM、但对话癌仍 ≥50 时跳过专项修复
        # —— 因为 needs_llm=True 阻断 path。改为：对话癌是独立维度，
        # 触发后必须再调一次 LLM（允许替换次数 ≥ 2），让对话污染有机会消除。
        dialogue_replace_prompt = (
            NORMALIZER_SYSTEM
            + f"\n\n【对话癌专项】本章存在满篇对话提示词污染(count={dialogue_count}),"
            + "必须用以下 4 种方法之一替换:\n"
            + f"1. 动作卡位:{DIALOGUE_REPLACE_HINTS['动作卡位']}\n"
            + f"2. 神态神韵:{DIALOGUE_REPLACE_HINTS['神态神韵']}\n"
            + f"3. 情境穿插:{DIALOGUE_REPLACE_HINTS['情境穿插']}\n"
            + f"4. 语感辨识:{DIALOGUE_REPLACE_HINTS['语感辨识']}\n\n"
            + f"污染样本:{dialogue_samples[:5]}\n\n"
            + "直接输出改写后正文,不加任何说明。"
        )
        text, cost = router_call_for_dialogue(text, dialogue_replace_prompt)
        total_cost += cost
    elif dialogue_count >= DIALOGUE_WARNING_THRESHOLD:
        issues.append(
            f"对话癌预警(dialogue_pollution={dialogue_count}≥{DIALOGUE_WARNING_THRESHOLD}):"
            f"建议手动检查'某某说/道'使用频次"
        )

    # 2026-07-25 Commit 6：POV 视角切换密度检测
    pov_count, pov_switches = detect_pov_switching(text)
    pov_threshold = POV_SWITCH_MULTI_THRESHOLD if task.get("pov_multi") else POV_SWITCH_WARNING_THRESHOLD
    if pov_count > pov_threshold:
        issues.append(
            f"POV 视角切换超限(pov_switches={pov_count}>{pov_threshold}):"
            f"{pov_switches}。'穿越式跳视角'破坏代入感(战略审视 M6)。"
        )

    return text, issues, total_cost


def router_call_for_dialogue(text: str, dialogue_replace_prompt: str) -> tuple[str, float]:
    """对话癌专项 LLM 替换调用。

    独立 helper 而非内联,便于测试与替换。
    """
    from ..llm.router import LLMRouter
    from ..llm_router import get_active_router
    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    out, cost = router.call(
        agent_name="normalizer",
        system_prompt=dialogue_replace_prompt,
        user_prompt=f"请去除对话提示词污染,改写以下文本：\n\n{text}",
        max_tokens=len(text) * 2 + 300,
        temperature=0.5,
    )
    return out, cost