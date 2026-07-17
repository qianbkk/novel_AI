"""
读回 output/chapters/ch_<N>.txt + meta，触发既有的 embed + 人物标记 + 重复度检测——
独立于 novel_AI 自身的 Checker，两者并存，不是替代关系。score/rewrite_count
两个字段已从 run.py 的 cmd_show() 源码确认存在。

标题生成：meta.json 没有 title 字段（只有 chapter_role / chapter_goal），
我们自己从这两个字段派生"第N章 · {role} · {goal 前 30 字}"，确保章节管理页能
显示像样的标题，而不是"第1章 【修改后正文】"这种跑冒烟测试时的占位。
"""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Chapter
from ..rag.retrieval import add_chapter
from ..logging_setup import get_logger
from datetime import datetime, timezone

log = get_logger("novel_ai.chapter_import")


def _clean_content_for_import(content: str) -> str:
    """清理 chapter txt 的正文内容，去掉 chapter_import 阶段会污染显示的前缀：

    1. 行首的 [待修订] / [未通过] 标记（orchestrator 在 escalation 时加的）
    2. 整段是 JSON 包装 `{"title": "...", "body": "..."}` 的情况：剥外层取 body
    3. 行首既有 [待修订] + 紧跟 JSON 包装的情况：去掉 [待修订] 行再剥 JSON

    返回清洗后的正文（去掉了内容污染，但保留原始换行/段落）。
    """
    import re as _re
    if not content:
        return ""
    stripped = content.lstrip()

    # 剥 "[待修订]" / "[未通过]" 行前缀（可能多个）
    lines = stripped.split("\n")
    while lines and lines[0].strip() in ("[待修订]", "[未通过]"):
        lines = lines[1:]
    stripped = "\n".join(lines).lstrip()

    # 整段是 JSON 包装：剥外层取 body
    if stripped.startswith("{") and '"body"' in stripped[:200]:
        # 1) 先试严格解析
        try:
            import json as _json
            d = _json.loads(stripped)
            if isinstance(d, dict) and "body" in d:
                return str(d.get("body", ""))
        except Exception:
            pass
        # 2) 解析失败：writer raw 输出有真换行符（违反 JSON 语法），手动扫描抽 body。
        #    找 "body":" 起点，扫描到下一个非转义的 " 或 } 停止。
        m_start = _re.search(r'"body"\s*:\s*"', stripped)
        if m_start:
            i = m_start.end()
            out = []
            while i < len(stripped):
                ch = stripped[i]
                if ch == "\\" and i + 1 < len(stripped):
                    # 保留转义字符的反义（如 \" → "），\\ → \，\n → 真 newline
                    nxt = stripped[i+1]
                    if nxt == "n":
                        out.append("\n")
                    elif nxt == "r":
                        out.append("\r")
                    elif nxt == "t":
                        out.append("\t")
                    elif nxt == '"':
                        out.append('"')
                    elif nxt == "\\":
                        out.append("\\")
                    else:
                        out.append(nxt)
                    i += 2
                    continue
                if ch == '"' or ch == '}':
                    break
                out.append(ch)
                i += 1
            return "".join(out)

    return stripped


def _derive_title(n: int, meta: dict, content: str) -> str:
    """派生章节标题。

    修订 2026-07-16（第二轮）：
      1. meta.title（writer 2026-07-16 后会写）
      2. 内容首句抽取 — 即使有 chapter_goal，若 goal 是 placeholder 模板
         （「第N章：推进剧情」「发展·第N章：推进剧情」之类）就走首句
      3. role + 真实 chapter_goal 派生
      4. 兜底"第N章"

    修订 2026-07-17：meta.title 可能是 JSON `{"title": ..., "body": ...}`（writer
    2026-07-16 后输出 JSON 包装），需要剥 JSON 取 title 字段。
    """
    import json as _json

    # 1) meta.title（writer 直接给的最准）
    raw_title = (meta.get("title") or "").strip()
    if raw_title.startswith("{") and '"title"' in raw_title:
        try:
            d = _json.loads(raw_title)
            raw_title = d.get("title") or d.get("body", "")[:30]
        except Exception:
            raw_title = raw_title[:40]
    if raw_title and raw_title not in ("未命名章节",):
        return f"第{n}章·{raw_title[:40]}"

    # 2) 内容首句抽取（跳过 junk 行）—— 即使有 chapter_goal，
    #    若 goal 是 placeholder 模板（无意义）也走这条路径拿真实标题
    content_title = _extract_title_from_content(content)
    goal = (meta.get("chapter_goal") or "").strip()
    is_placeholder_goal = _is_placeholder_goal(goal)

    if content_title and (is_placeholder_goal or not goal):
        return f"第{n}章·{content_title[:40]}"

    # 3) role + 真实 chapter_goal 派生
    role = (meta.get("chapter_role") or "").strip()
    if goal and not is_placeholder_goal:
        goal_short = goal[:30] + ("…" if len(goal) > 30 else "")
        return f"第{n}章·{role or '正文'}·{goal_short}"

    # 4) 兜底：仅 role 或仅内容
    if role and content_title:
        return f"第{n}章·{role}·{content_title[:30]}"
    if content_title:
        return f"第{n}章·{content_title[:40]}"
    if role:
        return f"第{n}章·{role}"
    return f"第{n}章"


def _is_placeholder_goal(goal: str) -> bool:
    """判断 chapter_goal 是否是 placeholder 模板（无信息量）。

    已知 placeholder 模式：
      - "第N章：推进剧情"
      - "第N章·xxx：推进剧情"（orchestrator placeholder_task 早期版）
      - "...：推进剧情"（变体）
    """
    import re as _re
    if not goal:
        return True
    g = goal.strip()
    # "推进剧情" 是 placeholder 的核心信号
    if "推进剧情" in g:
        return True
    # "第N章：xxx" / "第N章 xxx" 且长度很短（≤15 字）
    if _re.match(r"^第\d+[章卷][\s::：]\S{0,8}$", g):
        return True
    return False


def _extract_title_from_content(content: str) -> str:
    """从正文首段抽取一个像样的标题（≤ 30 字）。

    跳过：
      1. 空行 / 太短的行
      2. 纯 scene label 行（"【xxx】"，不带正文的）
      3. 「第N章 标题」/「第N卷 xxx」类重复标题（包括"【卷名】第N章 标题"复合形式）
      4. Markdown 标题行（"# 第七章 xxx"）
      5. "[待修订]" / "[未通过]" 前缀
    """
    import re as _re
    import json as _json
    if not content:
        return ""
    # 6) 先剥 JSON 包装（writer 2026-07-16 后输出 JSON {"title": ..., "body": ...}）。
    #    disk 上内容可能是 "[待修订]\n{...JSON...}"，先去掉 [待修订] 前缀再尝试 JSON 解析。
    stripped = content.lstrip()
    for prefix in ("[待修订]\n", "[未通过]\n"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
    stripped = stripped.lstrip()
    if stripped.startswith("{") and '"title"' in stripped[:200]:
        # 1) 先试严格解析
        try:
            d = _json.loads(stripped)
            if isinstance(d, dict) and "title" in d:
                return str(d.get("title", ""))[:30].strip()
        except Exception:
            pass
        # 2) 解析失败时用 regex 兜底抽 "title" 字段（处理 writer 输出有
        #    双重转义 \\n 等坏 JSON 的情况）
        m_title = _re.search(r'"title"\s*:\s*"([^"\\]+(?:\\.[^"\\]*)*)"', stripped)
        if m_title:
            return m_title.group(1).replace('\\n', '').replace('\\"', '"')[:30].strip()
    junk_patterns = [
        _re.compile(r"^第\d+[章卷]\s*\S+"),
        _re.compile(r"^【[^】]+】第\d+[章卷]\s*\S+"),
        _re.compile(r"^#{1,6}\s+第?\d*[章卷]?\s*\S*"),
        _re.compile(r"^#{1,6}\s+\S+"),
        _re.compile(r"^---+$"),
        _re.compile(r"^\[待修订\]"),
        _re.compile(r"^\[未通过\]"),
    ]
    for line in content.splitlines():
        s = line.strip()
        if not s or len(s) <= 4:
            continue
        if s.startswith("【") and s.endswith("】") and " " not in s and len(s) <= 30:
            continue
        if any(p.match(s) for p in junk_patterns):
            continue
        # 去掉 markdown heading 前缀
        s = _re.sub(r"^#{1,6}\s+", "", s)
        # 截到第一个句号/问号/感叹号
        s = _re.split(r"[。！？!?]", s)[0].strip()
        if not s or len(s) <= 2:
            continue
        return s[:30]
    return ""


def _build_summary(meta: dict, content: str) -> str:
    """从 meta 派生章节 summary。meta.chapter_goal 优先；缺则用 status/word_count 兜底；
    全无则用正文首句。绝不返回空字符串。"""
    goal = (meta.get("chapter_goal") or "").strip()
    if goal:
        return goal[:120]
    status = (meta.get("status") or "").strip()
    if status == "human_required":
        return "本章评分未达标（status=human_required），需人工补全。"
    # 兜底：正文首句非空
    for line in (content or "").splitlines():
        s = line.strip()
        if s and not s.startswith("【") and len(s) > 8:
            return s[:120]
    return f"本章 {len(content or '')} 字，正文已生成。"


async def import_chapters_from_novel_ai(project_id: str, novel_ai_dir: str, db: Session) -> list[dict]:
    imported = []
    chapters_dir = Path(novel_ai_dir, "output", "chapters")
    if not chapters_dir.exists():
        log.warning("import-chapters: %s 不存在", chapters_dir)
        return imported

    # force=True 时会覆盖已有行（更新 title/content/summary），用于"修了章节管理显示"场景
    force = False  # 调用方通过 import_chapters_force 单独传

    for txt_path in sorted(chapters_dir.glob("ch_*.txt")):
        # 迭代 #31：每个文件独立 try/except，单文件坏不能阻断整批 import。
        # 之前一行错就全抛异常 → 用户看到的现象是"0 章导入"，没法定位是哪个文件坏。
        try:
            # 文件名 ch_<N>.txt → 取 N；malformed 跳过
            try:
                n = int(txt_path.stem.split("_")[1])
            except (IndexError, ValueError):
                log.warning("import-chapters: 跳过畸形文件名 %s", txt_path.name)
                continue

            content = txt_path.read_text(encoding="utf-8")
            # 一期修复（前端展示）：剥 [待修订] 前缀 + JSON 包装
            # —— writer 2026-07-16 后输出 {"title":..., "body":...}，
            # orchestrator.save_chapter 把原始 LLM 输出落盘（含 JSON 包装），
            # import 时需要剥 JSON 才能给前端纯文本 body。
            content = _clean_content_for_import(content)
            meta_path = txt_path.with_name(txt_path.stem + "_meta.json")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                # meta 损坏不能阻断 txt 导入 — 没 meta 仍能 derive title/summary
                log.warning("import-chapters: %s meta.json 损坏（%s），跳过 meta", txt_path.name, e)
                meta = {}

            existing = db.query(Chapter).filter_by(project_id=project_id, chapter_no=n).first()
            if existing and not force:
                continue  # 已经导入过，跳过——避免重复 embed 同一章

            # 派生一个像样的标题，避免显示"【修改后正文】"
            derived_title = _derive_title(n, meta, content)
            derived_summary = _build_summary(meta, content)

            if existing:
                # 覆盖：保留 id，更新内容 + 标题 + 摘要
                existing.title = derived_title
                existing.content = content
                existing.summary = derived_summary
                db.commit()
                imported.append({
                    "chapter_id": existing.id,
                    "chapter_no": n,
                    "title": derived_title,
                    "novel_ai_score": meta.get("score"),
                    "novel_ai_rewrite_count": meta.get("rewrite_count"),
                    "mode": "overwrite",
                })
                continue

            result = await add_chapter(project_id, n, derived_title, content, db)
            result["novel_ai_score"] = meta.get("score")
            result["novel_ai_rewrite_count"] = meta.get("rewrite_count")
            imported.append(result)
        except Exception as e:
            # 兜底：单文件 import 失败不能阻断整批
            log.exception("import-chapters: %s 处理失败（%s）", txt_path.name, e)
            continue

    log.info("import-chapters project=%s, imported=%d, dir=%s",
             project_id, len(imported), chapters_dir)
    return imported


async def _force_reimport(project_id: str, novel_ai_dir: str, db: Session) -> list[dict]:
    """强制重新导入：覆盖已有行的 title/content/summary。专用于修章节管理显示。"""
    chapters_dir = Path(novel_ai_dir, "output", "chapters")
    if not chapters_dir.exists():
        log.warning("_force_reimport: %s 不存在", chapters_dir)
        return []

    updated = []
    for txt_path in sorted(chapters_dir.glob("ch_*.txt")):
        # 迭代 #31：同 import_chapters_from_novel_ai，单文件坏不能阻断整批
        try:
            try:
                n = int(txt_path.stem.split("_")[1])
            except (IndexError, ValueError):
                log.warning("_force_reimport: 跳过畸形文件名 %s", txt_path.name)
                continue

            content = txt_path.read_text(encoding="utf-8")
            # 一期修复（前端展示）：剥 [待修订] 前缀 + JSON 包装（与 import_chapters 一致）
            content = _clean_content_for_import(content)
            meta_path = txt_path.with_name(txt_path.stem + "_meta.json")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                log.warning("_force_reimport: %s meta.json 损坏（%s），跳过 meta", txt_path.name, e)
                meta = {}

            derived_title = _derive_title(n, meta, content)
            derived_summary = _build_summary(meta, content)
            existing = db.query(Chapter).filter_by(project_id=project_id, chapter_no=n).first()
            if existing:
                existing.title = derived_title
                existing.content = content
                existing.summary = derived_summary
                if not existing.created_at:
                    existing.created_at = datetime.now(timezone.utc)
                db.commit()
                updated.append({"chapter_no": n, "title": derived_title, "mode": "updated"})
            else:
                from ..rag.retrieval import add_chapter
                await add_chapter(project_id, n, derived_title, content, db)
                updated.append({"chapter_no": n, "title": derived_title, "mode": "created"})
        except Exception as e:
            log.exception("_force_reimport: %s 处理失败（%s）", txt_path.name, e)
            continue
    log.info("_force_reimport project=%s, updated=%d", project_id, len(updated))
    return updated
