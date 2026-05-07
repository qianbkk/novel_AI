"""
Normalizer Agent V2 — 去AI腔处理
三道防线 + 集成文风指纹统计检测
"""
import re, os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import call_llm

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

def first_pass_replace(text: str) -> tuple[str, int]:
    count = 0
    for bad, goods in AI_WORDS.items():
        if bad in text:
            good = random.choice([g for g in goods if g] or [""])
            text = text.replace(bad, good)
            count += 1
    return text, count

def second_pass_llm(text: str) -> tuple[str, float]:
    out, cost = call_llm(
        agent_name="normalizer",
        system_prompt=NORMALIZER_SYSTEM,
        user_prompt=f"请去AI腔处理以下文本：\n\n{text}",
        max_tokens=len(text)*2+300,
        temperature=0.5,
    )
    return out, cost

def third_pass_format(text: str, task: dict) -> tuple[str, list]:
    issues = []
    paras = [p.strip() for p in text.split('\n') if p.strip()]
    long_p = [i+1 for i,p in enumerate(paras) if len(p)>150 and not p.startswith('【')]
    if long_p:
        issues.append(f"段落过长(>150字)：第{long_p[:3]}段")
    dl = sum(len(p) for p in paras if p.startswith(('"','「','"')) or '说' in p[:5] or p[-1:] in ('」','"'))
    total = sum(len(p) for p in paras)
    if total and dl/total < 0.25:
        issues.append(f"对话比例偏低({dl/total:.0%})")
    target = str(task.get("target_length","2000-2200"))
    lo,hi = (map(int,target.split("-")) if "-" in target else (int(target)-200,int(target)+200))
    actual = sum(len(p) for p in paras)
    if actual < lo:
        issues.append(f"字数不足({actual}<{lo})")
    elif actual > hi+500:
        issues.append(f"字数超标({actual}>{hi+500})")
    return text, issues

def run_normalizer(raw_text: str, task: dict) -> tuple[str, list, float]:
    total_cost = 0.0
    text, replace_count = first_pass_replace(raw_text)
    needs_llm = replace_count > 3 or any(p in text for p in AI_PATTERNS)
    if needs_llm:
        text, cost = second_pass_llm(text)
        total_cost += cost
    text, issues = third_pass_format(text, task)
    return text, issues, total_cost
