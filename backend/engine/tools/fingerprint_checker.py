"""tools/fingerprint_checker.py — 文风指纹统计检测 + 角色口癖执行检测

Migrated from novel_AI/tools/fingerprint_checker.py. Pure statistics,
no LLM dependency — runs as a deterministic post-write audit.
"""
from __future__ import annotations
import glob as g
import json
import os
import re
import statistics
from collections import Counter

from ..config.paths import CHAPTERS_DIR_STR, SETTING_PATH_STR, OUTPUT_DIR_STR


REPORTS_DIR = os.path.join(OUTPUT_DIR_STR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

AI_DIALOGUE_LEADS = ["说道", "回答道", "答道", "笑道", "冷声道", "沉声道", "轻声道",
                     "低声道", "高声道", "缓缓道", "淡淡道", "幽幽道"]

# The AI words dict comes from the normalizer agent. Importing it would
# pull in the LLM router which we don't need for stats-only analysis.
# Re-declare the keys list here for the scanner.
AI_WORD_KEYS = [
    "此刻", "蓦然", "不禁", "心中一动", "深吸一口气", "不由得", "莫名",
    "涌上心头", "眼眸", "嘴角微扬", "眸子", "沉声", "缓缓", "悄然",
    "骤然", "凝视", "喃喃", "霎时", "不料", "倏地", "话音刚落",
    "此话一出", "正因如此", "话虽如此", "不得不承认", "归根结底",
]


def analyze_fingerprint(text: str) -> dict:
    paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("【")]
    if not paragraphs:
        return {"ai_score": 0, "ai_level": "低风险", "flags": [], "details": {}}

    flags: list[str] = []
    details: dict = {}

    # 句子长度 std
    sentences = re.split(r'[。！？…]', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    if len(sentences) >= 5:
        lengths = [len(s) for s in sentences]
        std_dev = statistics.stdev(lengths) if len(lengths) > 1 else 0
        avg_len = statistics.mean(lengths)
        details["sentence_length_std"] = round(std_dev, 1)
        details["sentence_length_avg"] = round(avg_len, 1)
        if std_dev < 12 and len(sentences) > 10:
            flags.append(f"句子长度过于均匀（std={std_dev:.1f}），疑似AI")
        if avg_len > 45:
            flags.append(f"平均句长过长（{avg_len:.0f}字），疑似AI")

    # 段落首字多样性
    first_chars = [p[0] for p in paragraphs if p]
    first_char_counts = Counter(first_chars)
    if first_char_counts:
        mc, mcount = first_char_counts.most_common(1)[0]
        ratio = mcount / max(len(paragraphs), 1)
        details["most_common_para_start"] = f"「{mc}」({mcount}次/{len(paragraphs)}段={ratio:.0%})"
        if ratio > 0.25 and len(paragraphs) >= 8:
            flags.append(f"段落开头「{mc}」重复{mcount}次({ratio:.0%})，多样性不足")

    # 对话引导词
    ai_lead_count = sum(text.count(w) for w in AI_DIALOGUE_LEADS)
    details["ai_dialogue_leads"] = ai_lead_count
    if ai_lead_count > 5:
        flags.append(f"AI对话引导词过多（{ai_lead_count}次）")

    # 标点密度
    excl = text.count("！")
    ell = text.count("……") + text.count("…")
    char_count = max(len(text), 1)
    excl_ratio = excl / char_count * 100
    details["exclamation_per_100chars"] = round(excl_ratio, 2)
    if excl_ratio > 1.5:
        flags.append(f"感叹号密度过高（{excl_ratio:.1f}个/百字）")
    if ell > 8:
        flags.append(f"省略号过多（{ell}次）")

    # AI 高危词
    found = [w for w in AI_WORD_KEYS if w in text]
    details["ai_words_found"] = found
    if found:
        flags.append(f"发现AI高危词汇：{'、'.join(found)}")

    ai_score = min(100, len(flags) * 18 + len(found) * 8)
    return {
        "ai_score": ai_score,
        "ai_level": "高风险" if ai_score >= 60 else "中风险" if ai_score >= 30 else "低风险",
        "flags": flags,
        "details": details,
    }


def check_character_voices(text: str, task: dict, setting: dict) -> dict:
    if not setting:
        return {"ok": True, "missing_quirks": [], "found": {}}

    mc = setting.get("protagonist", {})
    key_chars = {c["name"]: c for c in setting.get("key_characters", [])}
    main_chars = task.get("main_characters", []) or []
    missing: list = []
    found: dict = {}

    to_check: list = []
    mc_name = mc.get("name", "")
    if mc_name and mc_name in main_chars:
        to_check.append((mc_name, mc.get("speech_quirks", [])))
    for char_name in main_chars:
        if char_name in key_chars:
            quirks = key_chars[char_name].get("speech_quirks", [])
            if quirks:
                to_check.append((char_name, quirks))

    for char_name, quirks in to_check:
        if not quirks:
            continue
        char_found = []
        for quirk in quirks[:2]:
            key_part = quirk[:8] if len(quirk) > 8 else quirk
            if key_part in text:
                char_found.append(quirk)
        found[char_name] = char_found
        if not char_found and char_name in text:
            missing.append({"character": char_name, "quirks": quirks[:1]})

    return {"ok": len(missing) == 0, "missing_quirks": missing, "found": found}


def run_fingerprint_check(text: str, task: dict = None, setting: dict = None) -> dict:
    fp = analyze_fingerprint(text)
    voice = check_character_voices(text, task or {}, setting or {})

    suggestions: list = []
    if fp["ai_score"] >= 60:
        suggestions.append("文风AI嫌疑较高，建议触发Normalizer深度处理")
    if fp["ai_score"] >= 30:
        for flag in fp["flags"][:3]:
            suggestions.append(f"修复：{flag}")
    if not voice["ok"]:
        for m in voice["missing_quirks"]:
            suggestions.append(f"补充{m['character']}的口癖体现：{m['quirks']}")

    return {
        "fingerprint": fp,
        "voice": voice,
        "overall_pass": fp["ai_score"] < 60 and voice["ok"],
        "suggestions": suggestions,
    }


def cmd_check(filepath: str) -> None:
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    setting = {}
    if os.path.exists(SETTING_PATH_STR):
        with open(SETTING_PATH_STR, encoding="utf-8") as f:
            setting = json.load(f)
    result = run_fingerprint_check(text, {}, setting)
    fp = result["fingerprint"]
    print(f"\n{'─'*50}")
    print(f"  文风指纹分析：{os.path.basename(filepath)}")
    print(f"{'─'*50}")
    print(f"  AI嫌疑分：{fp['ai_score']}/100  [{fp['ai_level']}]")
    if fp["flags"]:
        print(f"  发现问题：")
        for flag in fp["flags"]:
            print(f"    ⚠️  {flag}")
    else:
        print(f"  ✅ 未发现AI特征")
    v = result["voice"]
    if not v["ok"]:
        print(f"\n  口癖缺失：")
        for m in v["missing_quirks"]:
            print(f"    {m['character']}: {m['quirks']}")
    print(f"\n  整体通过：{'✅' if result['overall_pass'] else '❌'}")
    if result["suggestions"]:
        print(f"  建议：")
        for s in result["suggestions"]:
            print(f"    → {s}")


def cmd_scan() -> None:
    files = sorted(g.glob(os.path.join(CHAPTERS_DIR_STR, "ch_????.txt")))
    if not files:
        print("无章节文件")
        return
    setting = {}
    if os.path.exists(SETTING_PATH_STR):
        with open(SETTING_PATH_STR, encoding="utf-8") as f:
            setting = json.load(f)
    high_risk = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        ch = int(re.search(r'\d+', os.path.basename(fp)).group())
        result = run_fingerprint_check(text, {}, setting)
        score = result["fingerprint"]["ai_score"]
        if score >= 30:
            high_risk.append((ch, score))
            flag = result["fingerprint"]["flags"][0] if result["fingerprint"]["flags"] else ""
            print(f"  ⚠️  Ch{ch:4d}: AI嫌疑{score}分 {flag}")
        else:
            print(f"  ✅  Ch{ch:4d}: AI嫌疑{score}分")
    print(f"\n  高风险章节（≥60分）：{sum(1 for _, s in high_risk if s >= 60)}")
    print(f"  中风险章节（≥30分）：{sum(1 for _, s in high_risk if s >= 30)}")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args or args[0] == "scan":
        cmd_scan()
    elif args[0] == "check" and len(args) > 1:
        cmd_check(args[1])
    else:
        print(__doc__)