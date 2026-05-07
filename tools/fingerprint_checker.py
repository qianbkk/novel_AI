"""
tools/fingerprint_checker.py — 文风指纹统计检测 + 角色口癖执行检测
V3方案5.4/5.5节：不依赖AI主观判断，用统计方法检测AI腔

检测项：
  A. 文风指纹（统计）
     - 句子长度分布（AI倾向均匀，人类波动大）
     - 段落首字多样性（AI倾向重复用「他/她/主角名」开头）
     - 标点密度比（AI感叹号/省略号过多）
     - 对话引导词多样性（AI反复用「说道/回答」）

  B. 角色口癖检测
     - 检查本章出场人物的口癖是否出现（至少各1次）

运行：
  python tools/fingerprint_checker.py check output/chapters/ch_0001.txt
  python tools/fingerprint_checker.py scan   （扫描所有章节）
"""
import os, sys, re, json
from collections import Counter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

SETTING_PATH = os.path.join(BASE_DIR, "output", "setting_package.json")
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")
REPORTS_DIR  = os.path.join(BASE_DIR, "output", "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════
# A. 文风指纹统计检测
# ═══════════════════════════════════════════════
AI_DIALOGUE_LEADS = ["说道", "回答道", "答道", "笑道", "冷声道", "沉声道", "轻声道",
                     "低声道", "高声道", "缓缓道", "淡淡道", "幽幽道"]
HUMAN_DIALOGUE_LEADS = ["说", "道", "开口", "接口", "补充", "反问", "追问", "插嘴"]

def analyze_fingerprint(text: str) -> dict:
    """
    统计文风指纹，返回各项指标和综合AI嫌疑分数（0-100，越高越像AI）
    """
    paragraphs = [p.strip() for p in text.split("\n") if p.strip() and not p.startswith("【")]
    if not paragraphs:
        return {"ai_score": 0, "flags": [], "details": {}}

    flags = []
    details = {}

    # ── 1. 句子长度分布（标准差，越小越像AI）──
    import statistics
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

    # ── 2. 段落首字分析 ──
    first_chars = [p[0] for p in paragraphs if p]
    first_char_counts = Counter(first_chars)
    most_common_char, most_common_count = first_char_counts.most_common(1)[0] if first_char_counts else ("", 0)
    first_char_ratio = most_common_count / max(len(paragraphs), 1)
    details["most_common_para_start"] = f"「{most_common_char}」({most_common_count}次/{len(paragraphs)}段={first_char_ratio:.0%})"
    if first_char_ratio > 0.25 and len(paragraphs) >= 8:
        flags.append(f"段落开头「{most_common_char}」重复{most_common_count}次({first_char_ratio:.0%})，多样性不足")

    # ── 3. 对话引导词分析 ──
    ai_lead_count = sum(text.count(w) for w in AI_DIALOGUE_LEADS)
    details["ai_dialogue_leads"] = ai_lead_count
    if ai_lead_count > 5:
        flags.append(f"AI对话引导词过多（{ai_lead_count}次）：" +
                     "、".join(w for w in AI_DIALOGUE_LEADS if text.count(w) > 1)[:40])

    # ── 4. 标点密度 ──
    exclamations = text.count("！")
    ellipses     = text.count("……") + text.count("…")
    char_count   = max(len(text), 1)
    excl_ratio   = exclamations / char_count * 100
    details["exclamation_per_100chars"] = round(excl_ratio, 2)
    if excl_ratio > 1.5:
        flags.append(f"感叹号密度过高（{excl_ratio:.1f}个/百字）")
    if ellipses > 8:
        flags.append(f"省略号过多（{ellipses}次），可能过度依赖省略号")

    # ── 5. AI高危词汇检测 ──
    AI_WORDS = ["此刻","蓦然","不禁","心中一动","深吸一口气","不由得","莫名",
                "涌上心头","眼眸","嘴角微扬","眸子","倏地","霎时","骤然"]
    found_ai_words = [w for w in AI_WORDS if w in text]
    details["ai_words_found"] = found_ai_words
    if found_ai_words:
        flags.append(f"发现AI高危词汇：{'、'.join(found_ai_words)}")

    # ── 综合分 ──
    ai_score = min(100, len(flags) * 18 + (len(found_ai_words) * 8))

    return {
        "ai_score": ai_score,
        "ai_level": "高风险" if ai_score >= 60 else "中风险" if ai_score >= 30 else "低风险",
        "flags": flags,
        "details": details,
    }


# ═══════════════════════════════════════════════
# B. 角色口癖执行检测（V3方案5.4节）
# ═══════════════════════════════════════════════
def check_character_voices(text: str, task: dict, setting: dict) -> dict:
    """
    检查本章中主要出场人物的口癖是否出现
    """
    if not setting:
        return {"ok": True, "missing_quirks": []}

    mc = setting.get("protagonist", {})
    key_chars = {c["name"]: c for c in setting.get("key_characters", [])}
    main_chars = task.get("main_characters", [])

    missing = []
    found = {}

    all_chars_to_check = []
    mc_name = mc.get("name", "")
    if mc_name and mc_name in main_chars:
        all_chars_to_check.append((mc_name, mc.get("speech_quirks", [])))

    for char_name in main_chars:
        if char_name in key_chars:
            quirks = key_chars[char_name].get("speech_quirks", [])
            if quirks:
                all_chars_to_check.append((char_name, quirks))

    for char_name, quirks in all_chars_to_check:
        if not quirks:
            continue
        # 检查口癖是否出现（至少一条口癖出现1次以上）
        char_found = []
        for quirk in quirks[:2]:  # 最多检查前两条口癖
            # 口癖可能是行为描述，提取关键词
            key_part = quirk[:8] if len(quirk) > 8 else quirk
            if key_part in text:
                char_found.append(quirk)
        found[char_name] = char_found
        if not char_found and char_name in text:  # 人物出场了但口癖没出现
            missing.append({"character": char_name, "quirks": quirks[:1]})

    return {
        "ok": len(missing) == 0,
        "missing_quirks": missing,
        "found": found,
    }


# ═══════════════════════════════════════════════
# 综合检查入口
# ═══════════════════════════════════════════════
def run_fingerprint_check(text: str, task: dict = None, setting: dict = None) -> dict:
    """
    返回完整的指纹检查结果
    {
      fingerprint: {...},
      voice: {...},
      overall_pass: bool,
      suggestions: [...]
    }
    """
    fp = analyze_fingerprint(text)
    voice = check_character_voices(text, task or {}, setting or {})

    suggestions = []
    if fp["ai_score"] >= 60:
        suggestions.append("文风AI嫌疑较高，建议触发Normalizer深度处理")
    if fp["ai_score"] >= 30:
        for flag in fp["flags"][:3]:
            suggestions.append(f"修复：{flag}")
    if not voice["ok"]:
        for m in voice["missing_quirks"]:
            suggestions.append(f"补充{m['character']}的口癖体现：{m['quirks']}")

    overall_pass = fp["ai_score"] < 60 and voice["ok"]

    return {
        "fingerprint": fp,
        "voice": voice,
        "overall_pass": overall_pass,
        "suggestions": suggestions,
    }


# ═══════════════════════════════════════════════
# 命令行
# ═══════════════════════════════════════════════
def cmd_check(filepath: str):
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    setting = {}
    if os.path.exists(SETTING_PATH):
        with open(SETTING_PATH, encoding="utf-8") as f:
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


def cmd_scan():
    import glob as g
    files = sorted(g.glob(os.path.join(CHAPTERS_DIR, "ch_????.txt")))
    if not files:
        print("无章节文件"); return
    setting = {}
    if os.path.exists(SETTING_PATH):
        with open(SETTING_PATH, encoding="utf-8") as f:
            setting = json.load(f)
    high_risk = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        ch = int(re.search(r'\d+', os.path.basename(fp)).group())
        result = run_fingerprint_check(text, {}, setting)
        score = result["fingerprint"]["ai_score"]
        if score >= 30:
            high_risk.append((ch, score, result["fingerprint"]["flags"][:1]))
            print(f"  ⚠️  Ch{ch:4d}: AI嫌疑{score}分 {result['fingerprint']['flags'][0] if result['fingerprint']['flags'] else ''}")
        else:
            print(f"  ✅  Ch{ch:4d}: AI嫌疑{score}分")
    print(f"\n  高风险章节（≥60分）：{sum(1 for _,s,_ in high_risk if s>=60)}")
    print(f"  中风险章节（≥30分）：{sum(1 for _,s,_ in high_risk if s>=30)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "scan":
        cmd_scan()
    elif args[0] == "check" and len(args) > 1:
        cmd_check(args[1])
    else:
        print(__doc__)
