"""Title Generator Agent — 调用 LLM 为已有章节生成标题。

修订 2026-07-16：之前的 `_derive_title` 只是从内容首句机械截取，
本质还是正文不是标题。用户反馈「标题全都是正文」。

修法：调用 LLM 读章节内容前 1000 字 + meta.json 的 chapter_role，
让 LLM 生成一个 4-15 字的标题（事件/转折/冲突，不带「第N章」前缀）。

成本考虑：
- 300 章 / 每 10 章 batch / 每 batch ~500 tokens → 总共 ~30 次 LLM 调用
- 走 MiniMax-M3（用户已用），单次 ~$0.002 → 总成本 ~$0.06
- 比手动写便宜

跳过条件：
- chapter_role == 'human_required' 或 title 已包含 [待修订] → 跳过
- 标题已经像标题（≤15 字 + 不是首句机械截） → 跳过
"""
from __future__ import annotations
import logging
import re
from typing import List, Dict, Any, Tuple

from ..llm.router import LLMRouter
from ..llm_router import get_active_router

log = logging.getLogger("novel_ai.engine.title_generator")

TITLE_GEN_SYSTEM = """你是一位网文编辑。任务：为给定的小说章节生成一个 4-15 字的标题。

【标题要求】
- 必须抓本章核心事件 / 决策 / 冲突 / 转折
- 不能是「推进剧情」「发展剧情」这种通用词
- 不要「第N章」前缀
- 不要用句号、问号、感叹号
- 标题应该是事件或角色动作的浓缩（如「U盘证据」「周芸密会陆承」「冻结账户」）

【输出格式】严格 JSON：
{"title": "你的标题"}

只输出 JSON，不要任何 markdown fence 或额外说明。"""

TITLE_GEN_USER_TEMPLATE = """【章节信息】
定位：{role}
当前标题（如有）：{existing_title}

【章节内容（前 1000 字）】
{content_excerpt}

为这一章生成标题："""


def generate_title_for_chapter(
    chapter_no: int,
    content: str,
    meta: Dict[str, Any] | None = None,
) -> Tuple[str, float]:
    """为单章生成标题。返回 (title, cost_usd)。

    content 太短（< 50 字，如 [待修订] 占位）→ 直接返 fallback 不调 LLM。
    content 完全为空 → 返回 "空白章节"，让调用方知道这章没内容（而不是返空串导致
    API 认为 "新标题 == 旧标题" 而跳过记录）。
    """
    if not content:
        return "空白章节", 0.0
    if len(content.strip()) < 50:
        return _fallback_title(content), 0.0

    role = (meta or {}).get("chapter_role", "发展") if meta else "发展"
    existing = (meta or {}).get("title", "") if meta else ""

    # 内容前 1000 字足够生成标题
    excerpt = content.strip()[:1000]

    user_prompt = TITLE_GEN_USER_TEMPLATE.format(
        role=role,
        existing_title=existing or "（无）",
        content_excerpt=excerpt,
    )

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()

    try:
        resp, cost = router.call(
            agent_name="title_generator",
            system_prompt=TITLE_GEN_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=200,
            temperature=0.5,
        )
    except Exception as e:
        log.exception("title_generator LLM call failed for ch%d: %s", chapter_no, e)
        return _fallback_title(content), 0.0

    title = _parse_title_response(resp)
    if not title:
        return _fallback_title(content), cost
    return title, cost


def generate_titles_batch(
    chapters: List[Dict[str, Any]],
    on_progress: callable = None,
) -> List[Dict[str, Any]]:
    """批量生成标题。

    chapters: list of {chapter_no, content, meta} 字典
    on_progress: 可选回调 (idx, total, chapter_no, new_title) 用于前端展示进度
    返回：每个章节追加 {new_title, cost} 字段
    """
    results: List[Dict[str, Any]] = []
    total = len(chapters)
    total_cost = 0.0
    for i, ch in enumerate(chapters):
        new_title, cost = generate_title_for_chapter(
            ch["chapter_no"],
            ch.get("content", ""),
            ch.get("meta", {}),
        )
        total_cost += cost
        results.append({
            **ch,
            "new_title": new_title,
            "cost": cost,
        })
        if on_progress:
            try:
                on_progress(i + 1, total, ch["chapter_no"], new_title)
            except Exception:
                pass
    log.info("generate_titles_batch: %d chapters, total cost $%.4f", total, total_cost)
    return results


def _parse_title_response(resp: str) -> str:
    """从 LLM 响应解析 title（容忍 JSON / markdown fence / 裸字符串 / 多行）。"""
    import json as _json

    if not resp:
        return ""

    text = resp.strip()

    # 1) 严格 JSON（处理可能带 \n 后解释的情况）
    # 找第一个 { 到最后一个 } 的子串尝试 parse
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        candidate = text[start:end]
        try:
            d = _json.loads(candidate)
            if isinstance(d, dict):
                t = (d.get("title") or "").strip()
                if t:
                    return _sanitize_title(t)
        except _json.JSONDecodeError:
            pass

    # 2) markdown fence 包着的 JSON
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            d = _json.loads(m.group(1))
            t = (d.get("title") or "").strip()
            if t:
                return _sanitize_title(t)
        except _json.JSONDecodeError:
            pass

    # 3) 裸字符串（剥首尾引号 + 截到第一行）
    first_line = text.split("\n")[0].strip()
    # 剥首尾成对引号
    if (first_line.startswith('"') and first_line.endswith('"')) or \
       (first_line.startswith("'") and first_line.endswith("'")):
        first_line = first_line[1:-1]
    first_line = first_line.strip("「").strip("」")
    return _sanitize_title(first_line)


def _sanitize_title(raw: str) -> str:
    """清洗标题：去「第N章」前缀、截断到 15 字、去掉标点。"""
    if not raw:
        return ""
    s = raw.strip()
    # 去「第N章」「第N卷」前缀
    s = re.sub(r"^第\d+[章卷][\s::：]*", "", s)
    # 去 markdown heading
    s = re.sub(r"^#{1,6}\s+", "", s)
    # 去尾部标点
    s = s.rstrip("。！？!?")
    # 截到 15 字
    s = s[:15]
    return s.strip()


def _fallback_title(content: str) -> str:
    """LLM 调用失败 / 内容太短时的 fallback：从首句截。"""
    if not content:
        return ""
    s = content.strip()
    # 跳过 [待修订] 前缀
    if s.startswith("[待修订]"):
        return "待修订章节"
    for line in s.splitlines():
        line = line.strip()
        if not line or len(line) <= 4:
            continue
        if line.startswith("【") and line.endswith("】"):
            continue
        # 截到第一个句号
        line = re.split(r"[。！？!?]", line)[0]
        return line[:15]
    return "未命名章节"