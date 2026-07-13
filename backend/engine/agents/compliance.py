"""Compliance Agent — 平台合规检查

Migrated from novel_AI/agents/compliance_agent.py. Two-tier check:
  1. Regex keyword scan (free)
  2. LLM semantic review (only if no hard-reject found)

Rule files live in backend/engine/config/compliance_rules/*.json.
"""
from __future__ import annotations
import json
import os
import re

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..config.paths import COMPLIANCE_RULES_DIR, COMPLIANCE_RULES_DIR_STR


def load_rules(platform: str = "fanqie") -> dict:
    """Load platform-specific compliance rules JSON. Falls back to fanqie."""
    path = os.path.join(COMPLIANCE_RULES_DIR_STR, f"compliance_rules_{platform}.json")
    if not os.path.exists(path):
        path = os.path.join(COMPLIANCE_RULES_DIR_STR, "compliance_rules_fanqie.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 第一道：关键词快速扫描（无LLM）──
# Compiled from the rule JSON's hard_rejects + warnings (regex patterns).
def _compile_patterns(rules: dict) -> tuple[list, list]:
    hard_patterns = []
    for rule in rules.get("hard_rejects", []):
        for p in rule.get("patterns", []):
            hard_patterns.append((p, rule["id"], rule["desc"]))
    warn_patterns = []
    for rule in rules.get("warnings", []):
        for p in rule.get("patterns", []):
            warn_patterns.append((p, rule["id"], rule["desc"]))
    return hard_patterns, warn_patterns


def keyword_scan(text: str, platform: str = "fanqie") -> tuple[list, list]:
    rules = load_rules(platform)
    hard_patterns, warn_patterns = _compile_patterns(rules)
    hard_rejects = []
    warnings = []
    for pattern, rule_id, desc in hard_patterns:
        if re.search(pattern, text):
            hard_rejects.append({"rule": rule_id, "desc": desc, "auto": True})
    for pattern, rule_id, desc in warn_patterns:
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
    sample = text[:2000] + ("..." if len(text) > 2000 else "")
    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    resp, cost = router.call(
        agent_name="compliance",
        system_prompt=COMPLIANCE_SYSTEM,
        user_prompt=f"请审核以下章节内容（平台：{platform}）：\n\n{sample}",
        max_tokens=600,
        temperature=0.1,
    )
    resp = resp.strip()
    # 剥 fence（Phase 9+ code-review-2026-07-13 漏迁移：当时只迁了 outline /
    # manager / parse_llm_json_response，本次补完）
    from ..utils import strip_markdown_fence
    stripped = strip_markdown_fence(resp)
    if stripped:
        resp = stripped
    try:
        result = json.loads(resp)
    except Exception:
        # 迭代 #41: 之前 fake-pass（passed=True + 空 hard_rejects）。
        # 后果：LLM 检测到的 hard reject（如「未成年人性暗示」「详细血腥
        # 描写」）在 JSON parse 失败时全部丢失 → passed=True → 违规内容
        # 落盘 → 平台审查删书。
        # 修法：保守策略 — 解析失败时视为 FAIL，要求人工 / 重试审核。
        # 不再 silent pass；同时把 parse 错误塞到 hard_rejects 里让
        # orchestrator / 上层能看到真实原因。
        import logging
        logging.getLogger("novel_ai.compliance").warning(
            "compliance LLM JSON parse failed: %r",
            (resp or "")[:200],
        )
        return {
            "passed": False,
            "hard_rejects": [{
                "rule": "PARSE_ERROR",
                "desc": "LLM 合规审核响应 JSON 解析失败（数据完整性优先，宁可误报不放过）",
                "excerpt": (resp or "")[:80],
            }],
            "warnings": [],
            "suggestion": "请重跑合规检查，或检查 LLM provider 是否正常返回 JSON",
        }, cost
    return result, cost


def run_compliance(text: str, platform: str = "fanqie") -> tuple[dict, float]:
    """完整合规检查
    返回：(result_dict, cost)
    result_dict: {passed, hard_rejects, warnings, suggestion}
    """
    # 快速扫描
    hard_kw, warn_kw = keyword_scan(text, platform)

    # 若关键词已发现硬拒绝，直接返回（省LLM调用）
    if hard_kw:
        return {
            "passed": False,
            "hard_rejects": hard_kw,
            "warnings": warn_kw,
            "suggestion": "存在硬违规内容，需要重写对应段落",
        }, 0.0

    # LLM语义检查
    result, cost = llm_semantic_check(text, platform)

    # 合并关键词警告
    if warn_kw:
        result["warnings"] = result.get("warnings", []) + warn_kw

    # 最终判定
    result["passed"] = len(result.get("hard_rejects", [])) == 0
    return result, cost