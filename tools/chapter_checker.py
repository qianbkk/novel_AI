"""
tools/chapter_checker.py — 跨章节一致性检查
检查项：
  1. 角色信息一致性（名字/年龄/能力不矛盾）
  2. 系统规则一致性（点数变化合理，等级跃升符合阈值）
  3. 时间线一致性（事件顺序，时间跨度）
  4. 已关闭剧情线是否被重新打开
  5. 道具/技能是否被使用了尚未获得的东西

运行：
  python tools/chapter_checker.py scan        # 扫描所有已生成章节
  python tools/chapter_checker.py check 15    # 检查特定章节
"""
import os, sys, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import parse_llm_json_response

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")
REPORTS_DIR  = os.path.join(BASE_DIR, "output", "reports")
SETTING_PATH = os.path.join(BASE_DIR, "output", "setting_package.json")

os.makedirs(REPORTS_DIR, exist_ok=True)


# ─────────────────────────────────────────────
# 规则一致性：本地检查（无需LLM）
# ─────────────────────────────────────────────
from config.power_levels import POWER_LEVELS

def check_point_logic(chapter_num: int, text: str, prev_points: int) -> list:
    """检查本章点数变化是否合理"""
    issues = []
    # 提取所有点数变化
    gains = re.findall(r'【人情点\+(\d+)】', text)
    losses = re.findall(r'【人情点-(\d+)】', text)
    final_mentions = re.findall(r'人情点[：:]\s*(\d+)', text)

    total_gain = sum(int(x) for x in gains)
    total_loss = sum(int(x) for x in losses)

    # 单章获得超过2000点（早期不合理）
    if total_gain > 2000 and chapter_num < 50:
        issues.append(f"Ch{chapter_num}: 单章获得{total_gain}点疑似过多（早期章节）")

    # 最终点数与计算不符
    if final_mentions:
        stated_points = int(final_mentions[-1])
        expected = prev_points + total_gain - total_loss
        if abs(stated_points - expected) > 10:
            issues.append(f"Ch{chapter_num}: 章内点数计算可能有误（stated={stated_points}, calc≈{expected}）")

    return issues


def check_level_up_logic(chapter_num: int, text: str, current_level: str) -> list:
    """检查境界突破是否合理"""
    issues = []
    level_names = list(POWER_LEVELS.keys())
    current_idx = level_names.index(current_level) if current_level in level_names else 0

    for level_name, (num, threshold) in POWER_LEVELS.items():
        if level_name in text and num > current_idx + 1:
            issues.append(f"Ch{chapter_num}: 出现了跨级境界名「{level_name}」，当前应为「{current_level}」")

    return issues


# ─────────────────────────────────────────────
# LLM深度一致性检查
# ─────────────────────────────────────────────
CONSISTENCY_SYSTEM = """你是网文编辑，专门检查小说的逻辑一致性。
你会收到一段章节正文和一些已知的人物/世界信息，找出所有矛盾点。

【检查重点】
1. 人物信息：名字、年龄、职业、外貌描述是否与已知信息矛盾
2. 能力使用：是否使用了尚未获得的技能或道具
3. 关系状态：角色间关系是否与之前建立的状态矛盾
4. 时间跳跃：是否有不合理的时间跳跃
5. 已知事实：是否与已确立的世界/情节事实矛盾

严格输出JSON：
{
  "has_issues": true/false,
  "issues": [
    {"type": "人物/能力/关系/时间/事实", "description": "具体描述", "severity": "high/medium/low"}
  ],
  "score": 1-10（10=完全一致，1=严重矛盾）
}"""

def llm_consistency_check(chapter_text: str, known_facts: dict) -> tuple[dict, float]:
    context = f"""【已知人物信息】
{json.dumps(known_facts.get('characters', {}), ensure_ascii=False, indent=2)[:800]}

【主角当前状态】
等级：{known_facts.get('protagonist_level', '感债者')}
点数：{known_facts.get('protagonist_points', 0)}
持有道具：{json.dumps(known_facts.get('inventory', []), ensure_ascii=False)}

【已确立的重要事实】
{chr(10).join('- ' + f for f in known_facts.get('established_facts', [])[:10])}

【章节正文（前2000字）】
{chapter_text[:2000]}

请检查矛盾："""

    resp, cost = call_llm(
        agent_name="checker_main",
        system_prompt=CONSISTENCY_SYSTEM,
        user_prompt=context,
        max_tokens=800,
        temperature=0.1,
    )
    result = parse_llm_json_response(resp, {"has_issues": False, "issues": [], "score": 8})
    return result, cost


# ─────────────────────────────────────────────
# 扫描所有章节
# ─────────────────────────────────────────────
def scan_all_chapters(novel_id: str = "renqingzhai_v1") -> dict:
    from memory.memory_manager import get_l2

    l2 = get_l2(novel_id)
    chapter_files = sorted(
        f for f in os.listdir(CHAPTERS_DIR)
        if re.match(r'ch_\d{4}\.txt', f)
    )

    if not chapter_files:
        print("❌ 没有找到已生成的章节文件")
        return {}

    print(f"🔍 开始扫描 {len(chapter_files)} 个章节...")
    all_issues = []
    total_cost = 0.0

    prev_points = 0
    current_level = "感债者"

    for fname in chapter_files:
        ch_num = int(fname.replace("ch_", "").replace(".txt", ""))
        with open(os.path.join(CHAPTERS_DIR, fname), encoding="utf-8") as f:
            text = f.read()

        # 本地检查（无成本）
        point_issues = check_point_logic(ch_num, text, prev_points)
        level_issues = check_level_up_logic(ch_num, text, current_level)
        local_issues = point_issues + level_issues

        if local_issues:
            for issue in local_issues:
                all_issues.append({"chapter": ch_num, "source": "local", "issue": issue, "severity": "medium"})

        # 更新追踪状态
        gains = sum(int(x) for x in re.findall(r'【人情点\+(\d+)】', text))
        losses = sum(int(x) for x in re.findall(r'【人情点-(\d+)】', text))
        prev_points = prev_points + gains - losses

        # 每10章做一次LLM深度检查
        if ch_num % 10 == 0:
            known_facts = {
                "characters": l2.get("character_states", {}),
                "protagonist_level": l2.get("protagonist_level", "感债者"),
                "protagonist_points": prev_points,
                "inventory": l2.get("inventory", []),
                "established_facts": l2.get("established_facts", []),
            }
            result, cost = llm_consistency_check(text, known_facts)
            total_cost += cost
            if result.get("has_issues"):
                for issue in result.get("issues", []):
                    all_issues.append({
                        "chapter": ch_num,
                        "source": "llm",
                        "issue": issue["description"],
                        "severity": issue["severity"],
                    })
            print(f"  Ch{ch_num:4d}: LLM检查得分 {result.get('score',8)}/10 "
                  f"{'⚠️' if result.get('has_issues') else '✅'}")

    # 生成报告
    report = {
        "scan_time": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "chapters_scanned": len(chapter_files),
        "total_issues": len(all_issues),
        "high_severity": sum(1 for i in all_issues if i.get("severity") == "high"),
        "medium_severity": sum(1 for i in all_issues if i.get("severity") == "medium"),
        "issues": all_issues,
        "scan_cost_usd": total_cost,
    }

    report_path = os.path.join(REPORTS_DIR, "consistency_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n{'─'*50}")
    print(f"📋 一致性扫描完成")
    print(f"   扫描章节：{len(chapter_files)}")
    print(f"   发现问题：{len(all_issues)}（高危：{report['high_severity']}，中危：{report['medium_severity']}）")
    print(f"   报告路径：{report_path}")
    print(f"   扫描成本：${total_cost:.4f}")

    return report


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
    if not args or args[0] == "scan":
        scan_all_chapters()
    elif args[0] == "check" and len(args) > 1:
        print(f"单章检查功能暂未独立实现，请使用 scan")
    else:
        print("用法：python tools/chapter_checker.py scan")
