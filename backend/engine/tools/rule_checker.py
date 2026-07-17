"""backend/engine/tools/rule_checker.py — 三期零成本规则检查器

不调 LLM 的正则/统计层，对章节文本做「硬规则」预检，作为 LLM 质检的先验。
目标：
  1. 把重复成本高的「AI 陈词 / 八股」类问题降到可量化
  2. 给 checker 提供「已检测到问题清单」，避免 LLM 重复找茬
  3. 完全离线（<10ms/章），不增加任何 token

检测维度（每项 0-10 分，分越低问题越严重）：
  - cliche_density：AI 陈词密度（嘴角勾起、眸子、闪过一丝、不禁...）
  - sentence_open_diversity：句首多样性（连续多章重复开场扣分）
  - dialog_ratio：对话占比（<10% 或 >70% 偏离网文节奏）
  - opening_hook：开篇前 80 字是否吸引人
  - closing_hook：结尾是否落钩（信息钩/危机钩/反转钩）
  - paragraph_length：段落平均长度是否拖慢阅读节奏
"""
from __future__ import annotations

import re
from typing import Any


# AI 陈词清单（中等强度，全部为网络小说高发陈词）
CLICHE_PATTERNS = [
    r"嘴角(勾起|微微上扬|露出一抹)(弧度|笑意|微笑)",
    r"眼眸?中闪过",
    r"深吸一口(气|凉气)",
    r"不禁(心中|心头)?(一[动凛]|涌起|涌上)",
    r"此刻[他她它]",
    r"蓦然[他她它]?",
    r"眸子",
    r"心中(一动|涌上|泛起)",
    r"眼中(闪过|闪过一丝|闪过一抹)",
    r"露出一抹(不易察觉的)?(笑意|微笑|冷笑)",
    r"不由(得|自主)(感慨|颤抖|后退)",
    r"刀削(般的)?(面庞|脸庞)",
    r"剑眉星目",
    r"倾城(之)?(容|色)",
]
CLICHE_RE = re.compile("|".join(CLICHE_PATTERNS))


# 常见雷区开场
COMMON_OPENINGS = [
    "阳光透过",
    "晨光(微|初)露",
    "天(刚|色)微(明|亮)",
    "闹钟(响了|响起)",
    "一阵(急促|刺耳)?(铃声|闹铃)",
    "醒来",
    "窗外",
    "睡梦中",
    "梦里",
]


def analyze_chapter(text: str, prev_openings: list[str] | None = None) -> dict[str, Any]:
    """对单章文本运行规则层分析。返回 {score, issues, details}。"""
    issues: list[str] = []
    details: dict[str, Any] = {}

    # 1. 陈词密度（每 1000 字命中数）
    cliche_hits = CLICHE_RE.findall(text)
    cliche_per_1k = len(cliche_hits) / max(1, len(text) / 1000)
    details["cliche_hits"] = cliche_hits[:5]   # 留前 5 个做日志
    details["cliche_per_1k"] = round(cliche_per_1k, 2)
    if cliche_per_1k >= 5:
        issues.append(f"陈词过密（{cliche_per_1k:.1f}/千字）")
    cliche_score = max(0, 10 - int(cliche_per_1k))

    # 2. 句首多样性：拿首 60 字（去掉标点）与最近 3 章首 60 字比
    text_clean = text.lstrip()
    head = text_clean[:60]
    head_norm = re.sub(r"[\s，。；：「」『』！？　]+", "", head)
    prev_openings = prev_openings or []
    _norm = lambda s: re.sub(r"[\s，。；：「」『』！？　]+", "", s or "")
    overlap = 0
    for p in prev_openings:
        if not p:
            continue
        prev_norm = _norm(p)[:20]
        my_norm = head_norm[:20]
        if prev_norm and my_norm and (
            prev_norm == my_norm
            or (len(prev_norm) >= 10 and prev_norm[:10] == my_norm[:10])
        ):
            overlap += 1
    if overlap >= 1:
        issues.append(f"开场与近章雷同（{overlap} 处）")
    diversity_score = max(0, 10 - overlap * 3)

    # 3. 对话占比
    dialog_lines = len(re.findall(r"[「『\"][^\n]+[」』\"]", text))
    dialog_chars = sum(len(m.group()) for m in re.finditer(r"[「『\"][^\n]+[」』\"]", text))
    dialog_ratio = dialog_chars / max(1, len(text))
    details["dialog_ratio"] = round(dialog_ratio, 3)
    if dialog_ratio < 0.10:
        issues.append(f"对话过少（{dialog_ratio:.1%}）")
    elif dialog_ratio > 0.70:
        issues.append(f"对话过多（{dialog_ratio:.1%}）")
    dialog_score = 10 if 0.10 <= dialog_ratio <= 0.70 else 6

    # 4. 开篇 80 字钩子（极简启发式：含问号/感叹号/动词占多 = 好）
    opening = text[:80]
    opening_signals = (
        opening.count("？") + opening.count("!") + opening.count("！")
        + len(re.findall(r"[她他我你]", opening))
    )
    opening_score = min(10, 4 + opening_signals)

    # 5. 结尾 80 字是否落钩（包含「？」或「…」或「——」或人称指向）
    tail = text[-100:]
    tail_signals = (
        tail.count("？") + tail.count("……") + tail.count("——")
        + len(re.findall(r"[她他我你]", tail))
    )
    # 太短的正文不强制要求钩子信号（<300 字本身就短）
    if len(text) >= 800 and tail_signals < 2:
        issues.append("结尾钩子弱")
    closing_score = min(10, 4 + tail_signals * 2)

    # 6. 段落均长（太长 = 节奏拖沓）
    paragraphs = [p for p in text.split("\n") if p.strip()]
    avg_para_len = sum(len(p) for p in paragraphs) / max(1, len(paragraphs))
    details["avg_para_len"] = round(avg_para_len, 1)
    if avg_para_len > 300:
        issues.append(f"段落过长（均 {avg_para_len:.0f} 字）")
    para_score = 10 if avg_para_len <= 200 else (7 if avg_para_len <= 300 else 4)

    overall = round(
        cliche_score * 0.25
        + diversity_score * 0.15
        + dialog_score * 0.15
        + opening_score * 0.10
        + closing_score * 0.15
        + para_score * 0.20,
        1,
    )

    return {
        "score": overall,
        "issues": issues,
        "details": details,
        "first_60": head_norm,    # 给上游存入 L2，下章对比
    }


def format_issues_for_prompt(result: dict) -> str:
    """把规则层检测结果转成可注入 writer / checker prompt 的中文片段。"""
    if not result.get("issues"):
        return ""
    lines = ["【规则层预检】（无需重写，写作时可顺手规避）"]
    for issue in result["issues"]:
        lines.append(f"  ⚠ {issue}")
    if result.get("details", {}).get("cliche_hits"):
        lines.append("  ◆ 抽检到的陈词：" +
                     "；".join(["".join(h) for h in result["details"]["cliche_hits"]]))
    return "\n".join(lines) + "\n"
