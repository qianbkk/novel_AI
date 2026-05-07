"""
Compliance Agent — 平台合规检查
输入：规范化后文本
输出：{passed: bool, hard_rejects: [], warnings: [], suggestion: str}
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import call_llm

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

def load_rules(platform: str = "fanqie") -> dict:
    path = os.path.join(CONFIG_DIR, f"compliance_rules_{platform}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ── 第一道：关键词快速扫描（无LLM）──
HARD_KEYWORD_PATTERNS = [
    (r"(习近平|总书记|中央委员|政治局)", "HR-01", "政治人物影射"),
    (r"(器官.*摘除|开膛.*血流|内脏.*外露).{0,20}", "HR-02", "血腥描写"),
    (r"(法轮功|东突|藏独)", "HR-03", "敏感宗教/分裂组织"),
    (r"(幼女|萝莉|未成年.*性|小学生.*情欲)", "HR-04", "未成年性暗示"),
]

WARN_KEYWORD_PATTERNS = [
    (r"(割腕|自杀|了结生命).{0,30}", "W-04", "自伤描写"),
    (r"(上海|北京|广州|深圳).{0,10}(爆炸|连环杀|屠杀)", "HR-05", "真实地名+犯罪"),
]

def keyword_scan(text: str) -> tuple[list, list]:
    hard_rejects = []
    warnings = []
    for pattern, rule_id, desc in HARD_KEYWORD_PATTERNS:
        if re.search(pattern, text):
            hard_rejects.append({"rule": rule_id, "desc": desc, "auto": True})
    for pattern, rule_id, desc in WARN_KEYWORD_PATTERNS:
        if re.search(pattern, text):
            warnings.append({"rule": rule_id, "desc": desc, "auto": True})
    return hard_rejects, warnings

# ── 第二道：LLM语义审核 ──
COMPLIANCE_SYSTEM = """你是番茄小说的内容审核AI。你的任务是检查输入的小说章节是否违反平台规定。

【必须标记为硬拒绝的内容（hard_reject）】
- 现实政治人物的可识别影射
- 详细血腥器官描写（超过2句）
- 未成年人性暗示
- 真实地名+犯罪直接组合

【需要标记为警告的内容（warning）】
- 全章无正向出口（主角一直被压制无任何反击）
- 灵异描写超过500字但未标注题材
- 自伤相关描写

【输出格式】严格JSON，不加任何说明：
{
  "passed": true/false,
  "hard_rejects": [{"rule": "规则编号", "desc": "描述", "excerpt": "触发片段（20字以内）"}],
  "warnings": [{"rule": "规则编号", "desc": "描述"}],
  "suggestion": "如有问题，给出修改建议；若通过则为空字符串"
}"""

def llm_semantic_check(text: str, platform: str = "fanqie") -> tuple[dict, float]:
    # 仅传前2000字给LLM（节省Token，关键违规多在前段）
    sample = text[:2000] + ("..." if len(text) > 2000 else "")
    resp, cost = call_llm(
        agent_name="compliance",
        system_prompt=COMPLIANCE_SYSTEM,
        user_prompt=f"请审核以下章节内容（平台：{platform}）：\n\n{sample}",
        max_tokens=600,
        temperature=0.1,
    )
    resp = resp.strip()
    if resp.startswith("```"):
        resp = "\n".join(resp.split("\n")[1:])
        resp = resp.rstrip("`").strip()
    try:
        result = json.loads(resp)
    except:
        result = {"passed": True, "hard_rejects": [], "warnings": [], "suggestion": ""}
    return result, cost


def run_compliance(text: str, platform: str = "fanqie") -> tuple[dict, float]:
    """
    完整合规检查
    返回：(result_dict, cost)
    result_dict: {passed, hard_rejects, warnings, suggestion}
    """
    # 快速扫描
    hard_kw, warn_kw = keyword_scan(text)

    # 若关键词已发现硬拒绝，直接返回（省LLM调用）
    if hard_kw:
        return {
            "passed": False,
            "hard_rejects": hard_kw,
            "warnings": warn_kw,
            "suggestion": "存在硬违规内容，需要重写对应段落"
        }, 0.0

    # LLM语义检查
    result, cost = llm_semantic_check(text, platform)

    # 合并关键词警告
    if warn_kw:
        result["warnings"] = result.get("warnings", []) + warn_kw

    # 最终判定
    result["passed"] = len(result.get("hard_rejects", [])) == 0

    return result, cost
