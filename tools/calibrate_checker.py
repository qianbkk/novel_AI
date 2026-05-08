"""
tools/calibrate_checker.py — Checker基线校准工具（V3方案5.3节）

目的：确保三个Checker模型的评分口味与真实读者对齐
步骤：
  1. 准备校准集：人工添加10段「真人高分片段」和10段「AI腔片段」到 calibration/ 目录
  2. 运行校准：python tools/calibrate_checker.py run
  3. 查看结果：python tools/calibrate_checker.py report

校准通过标准（V3方案）：
  三模型对40段样本的评分，与人工判断（真人/AI二分类）一致率 > 80%
  具体：对真人片段评分 ≥7 的比例 > 80%；对AI片段评分 ≤5 的比例 > 80%
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import parse_llm_json_response

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB_DIR  = os.path.join(BASE_DIR, "calibration")
RESULT_DIR = os.path.join(BASE_DIR, "output", "reports")
os.makedirs(CALIB_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)


# ─────────────────────────────────────────────
# 内置校准样本（启动时如无外部样本则使用内置）
# ─────────────────────────────────────────────
BUILTIN_HUMAN_SAMPLES = [
    {
        "id": "human_01",
        "label": "human",
        "text": """陆承把那份合同推回去，连看都没再看一眼。
"条款三，第二款。"他说，"你们的律师应该告诉你这句话的意思。"
对面那个穿灰色西装的男人愣了一下，然后笑起来，笑声比他的表情更假。
"陆顾问，我们都是成年人，有些事不用说得这么清楚。"
"清楚一点好。"陆承站起来，"省得以后说我没告诉你。"
他走出去的时候，那个男人还坐在原地，脸上的笑容慢慢不见了。
走廊里安静。助理追上来，小声问怎么了。
陆承说，没什么，就是这单不接了。"""
    },
    {
        "id": "human_02",
        "label": "human",
        "text": """贺苗把茶杯放在桌上，没有说话。
窗外的临江市还是那副样子，高楼上的霓虹灯牌一闪一闪的，把她半张脸照得很亮，另半张在阴影里。
"你知道你祖父最后跟我说了什么吗？"她问。
陆承摇头。
"他说，等这件事完了，他请我吃一顿好的。"贺苗低下头，手指在茶杯边缘绕了一圈，"他说好的那家馆子，我去了三次。每次都是一个人。"
陆承没有说对不起，因为那不是他的错。
但他记住了这个细节。"""
    },
]

BUILTIN_AI_SAMPLES = [
    {
        "id": "ai_01",
        "label": "ai",
        "text": """此刻，陆承的心中不禁涌上一丝莫名的感慨。他深吸一口气，目光凝视着远处，眼眸中闪烁着复杂的光芒。
正因如此，他才深刻地明白了一个道理：在这个世界上，有些事情是无法避免的，正如人情债的存在一般，它早已深深地烙印在了每一个人的命运之中。
"话虽如此，我还是要去做。"他缓缓道，声音低沉而充满力量。
此话一出，在场所有人都不禁为之动容，纷纷投来敬佩的目光。陆承蓦然一笑，不由得感到一丝释然。"""
    },
    {
        "id": "ai_02",
        "label": "ai",
        "text": """贺苗的眼眸中闪烁着难以言说的情绪，她轻轻地叹了口气，嘴角微微上扬，露出一抹苦涩的微笑。
"说到底，这一切都是命运的安排。"她喃喃道，声音如同秋风中的落叶，带着无尽的惆怅。
陆承不禁心中一动，他深邃的目光与贺苗对视，彼此之间仿佛有千言万语，却又无从说起。
良久，他才缓缓开口："既然如此，我们又何必执着于过去呢？"
话音刚落，两人都陷入了沉默，整个房间的空气仿佛都凝固了一般。"""
    },
]


# ─────────────────────────────────────────────
# 校准评分器
# ─────────────────────────────────────────────
CALIBRATION_CHECKER_SYSTEM = """你是网络文学质量评审，专注于都市系统流类型。
对输入的段落进行评分（1-10分），重点关注：
- 是否有AI腔（套话、堆砌词、情感夸张）
- 人物对话是否自然有辨识度
- 行文节奏是否流畅

真人写的好文章应得7-10分；明显AI腔应得1-4分。

输出格式（仅JSON）：{"score": 分数, "reason": "一句话说明评分原因"}"""

def score_sample(text: str, agent: str) -> tuple[float, str, float]:
    resp, cost = call_llm(
        agent_name=agent,
        system_prompt=CALIBRATION_CHECKER_SYSTEM,
        user_prompt=f"请评分以下段落：\n\n{text}",
        max_tokens=200,
        temperature=0.1,
    )
    data = parse_llm_json_response(resp, {"score": 6.0, "reason": "解析失败"})
    return float(data["score"]), data.get("reason", ""), cost


# ─────────────────────────────────────────────
# 主校准流程
# ─────────────────────────────────────────────
def run_calibration():
    # 加载样本（外部 + 内置）
    samples = []
    for fname in sorted(os.listdir(CALIB_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(CALIB_DIR, fname), encoding="utf-8") as f:
                samples.extend(json.load(f) if isinstance(json.load(open(os.path.join(CALIB_DIR, fname), encoding="utf-8")), list) else [json.load(open(os.path.join(CALIB_DIR, fname), encoding="utf-8"))])

    if not samples:
        print("  无外部校准样本，使用内置样本（4条）")
        samples = BUILTIN_HUMAN_SAMPLES + BUILTIN_AI_SAMPLES
    else:
        print(f"  加载 {len(samples)} 条校准样本")

    checkers = ["checker_main", "checker_cross1", "checker_cross2"]
    results = {c: [] for c in checkers}
    total_cost = 0.0

    print(f"\n  开始校准（每样本 × 3个模型）...")
    for sample in samples:
        text = sample["text"]
        label = sample["label"]  # "human" or "ai"
        for checker in checkers:
            score, reason, cost = score_sample(text, checker)
            total_cost += cost
            # 判断是否与label一致
            if label == "human":
                correct = score >= 7.0
            else:  # ai
                correct = score <= 5.0
            results[checker].append({
                "sample_id": sample["id"],
                "label": label,
                "score": score,
                "correct": correct,
                "reason": reason,
            })
            icon = "✅" if correct else "❌"
            print(f"    [{checker[:12]:12s}] {sample['id']:12s} label={label} score={score:.1f} {icon}")

    # 统计准确率
    print(f"\n{'─'*55}")
    print(f"  校准结果")
    print(f"{'─'*55}")

    calibration_result = {"checkers": {}, "passed": True, "total_cost": round(total_cost, 4)}
    for checker, checker_results in results.items():
        total = len(checker_results)
        correct = sum(1 for r in checker_results if r["correct"])
        accuracy = correct / total * 100 if total else 0

        human_samples = [r for r in checker_results if r["label"] == "human"]
        ai_samples    = [r for r in checker_results if r["label"] == "ai"]
        human_acc = sum(1 for r in human_samples if r["score"] >= 7.0) / max(len(human_samples), 1) * 100
        ai_acc    = sum(1 for r in ai_samples    if r["score"] <= 5.0) / max(len(ai_samples), 1)    * 100

        passed = accuracy >= 80
        if not passed:
            calibration_result["passed"] = False

        flag = "✅ 通过" if passed else "❌ 不通过（建议调整提示词）"
        print(f"  {checker[:20]:20s}: 总准确率{accuracy:.0f}% | 真人识别{human_acc:.0f}% | AI识别{ai_acc:.0f}%  {flag}")

        calibration_result["checkers"][checker] = {
            "accuracy": round(accuracy, 1),
            "human_accuracy": round(human_acc, 1),
            "ai_accuracy": round(ai_acc, 1),
            "passed": passed,
        }

    print(f"\n  总成本：${total_cost:.4f}")
    print(f"  校准整体：{'✅ 通过' if calibration_result['passed'] else '❌ 需要调整'}")

    # 保存结果
    result_path = os.path.join(RESULT_DIR, "calibration_result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(calibration_result, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存：{result_path}")

    return calibration_result


def cmd_report():
    result_path = os.path.join(RESULT_DIR, "calibration_result.json")
    if not os.path.exists(result_path):
        print("  尚未运行校准，请先运行：python tools/calibrate_checker.py run")
        return
    with open(result_path, encoding="utf-8") as f:
        result = json.load(f)
    print(f"\n  上次校准结果：{'通过' if result['passed'] else '未通过'}")
    for checker, data in result.get("checkers", {}).items():
        print(f"  {checker[:20]:20s}: 总{data['accuracy']}% | 真人{data['human_accuracy']}% | AI{data['ai_accuracy']}%")


if __name__ == "__main__":
    # 加载.env
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()

    args = sys.argv[1:]
    if not args or args[0] == "run":
        run_calibration()
    elif args[0] == "report":
        cmd_report()
    else:
        print(__doc__)
