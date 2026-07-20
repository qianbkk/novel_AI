"""已有小说文本切章器（纯函数，无 IO / 无 DB）。

goal 2026-07-19 授权的「已有小说上传」第一步：用户把整本小说的纯文本
交给 API，这里确定性地按章节标题行切分。刻意不用 LLM——切章是格式
问题不是语义问题，正则足够且零成本、可测、可复现；后续的大纲/人物/
世界观提取才需要 LLM。

章号策略：**始终按出现顺序重编号**（start_chapter_no 起连续递增），
原始标题完整保留在 title 里。原因：分卷小说每卷重新从"第一章"计数、
番外/垫章导致原号不连续，直接采用原号会撞 (project_id, chapter_no)
唯一约束或产生空洞；顺序号 + 原标题保留信息量是最稳的折中。
"""
from __future__ import annotations

import re

# 中文章节标题：第<数字/中文数字>章|回|节，行首匹配。
# 标题行一般很短——长行里即使以「第三章」开头（如引用/回忆句），也
# 大概率是正文，用行长上限挡掉误切。
_CN_NUM = r"[0-9０-９〇零一二两三四五六七八九十百千]+"
_CN_HEADING_RE = re.compile(
    rf"^\s*第({_CN_NUM})[章回节]\s*[:：·\--—、.\s]?\s*(.*?)\s*$"
)
_EN_HEADING_RE = re.compile(r"^\s*Chapter\s+(\d+)\s*[:：·\--—.\s]?\s*(.*?)\s*$", re.IGNORECASE)
# 卷标记不是章：丢弃该行，不切分、不进正文
_VOLUME_RE = re.compile(rf"^\s*第({_CN_NUM})[卷部集]\s*[:：·\--—、.\s]?\s*(.*?)\s*$")

_MAX_HEADING_LINE_CHARS = 50   # 超过视为正文（引用句/回忆句保护）
_MIN_PREFACE_CHARS = 200       # 首个标题前的内容超过此长度 → 保留为「楔子」


def _match_heading(line: str) -> str | None:
    """行是章节标题 → 返回派生 title；否则 None。"""
    if len(line.strip()) > _MAX_HEADING_LINE_CHARS:
        return None
    m = _CN_HEADING_RE.match(line) or _EN_HEADING_RE.match(line)
    if not m:
        return None
    rest = (m.group(2) or "").strip()
    return rest if rest else line.strip()


def split_novel_text(text: str, start_chapter_no: int = 1) -> list[dict]:
    """把整本小说文本切成 [{chapter_no, title, content}, ...]。

    - 无任何标题行 → 整段作为单章返回（不丢内容）。
    - 首个标题前的前言 ≥ 200 字 → 作为「楔子」章保留；更短（书名/作者行
      之类）→ 丢弃。
    - 卷标记行（第N卷/部/集）丢弃，不切分。
    - 空正文的标题（标题连着标题）仍成章，content 为空字符串，由调用方
      决定是否导入。
    """
    if not text or not text.strip():
        return []

    sections: list[dict] = []       # {"title": str|None, "lines": [str]}
    current = {"title": None, "lines": []}
    for line in text.splitlines():
        if _VOLUME_RE.match(line) and len(line.strip()) <= _MAX_HEADING_LINE_CHARS:
            continue
        title = _match_heading(line)
        if title is not None:
            sections.append(current)
            current = {"title": title, "lines": []}
        else:
            current["lines"].append(line)
    sections.append(current)

    # sections[0] 是首个标题前的前言（title=None）
    preface = sections[0]
    body_sections = sections[1:]
    preface_text = "\n".join(preface["lines"]).strip()

    parts: list[dict] = []
    if not body_sections:
        # 全文没有任何章节标记 → 单章
        return [{
            "chapter_no": start_chapter_no,
            "title": f"第{start_chapter_no}章",
            "content": preface_text,
        }]
    if len(preface_text) >= _MIN_PREFACE_CHARS:
        parts.append({"title": "楔子", "content": preface_text})
    for sec in body_sections:
        parts.append({
            "title": sec["title"],
            "content": "\n".join(sec["lines"]).strip(),
        })

    for i, p in enumerate(parts):
        p["chapter_no"] = start_chapter_no + i
    return parts
