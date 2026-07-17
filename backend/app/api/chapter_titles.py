"""LLM 标题生成 API — 修订 2026-07-16 Issue #12。

之前 _derive_title 机械从内容首句截 → 用户反馈「标题全是正文」。
新端点：调 LLM 读章节内容生成真正像样的标题。

POST /projects/:id/regenerate-titles
  - body: {limit?: int, only_missing?: bool, chapter_nos?: [int]}
  - 行为：
    1. 从 DB 读 chapter 列表（按 chapter_no 升序）
    2. 过滤（only_missing 跳过已有像样标题的；chapter_nos 限定特定章节）
    3. 逐章调 engine.agents.title_generator.generate_title_for_chapter
    4. 写回 Chapter.title
    5. 返回 [{chapter_no, old_title, new_title, cost}]

  - 不阻塞 SSE，前端调完再 refresh list。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Chapter
from ..auth_scope import require_owned_project

log = logging.getLogger("novel_ai.api.chapter_titles")

def _owner_check(request: Request, project_id: str, db: Session = Depends(get_db)):
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode

    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        raise HTTPException(401, "authentication required")
    require_owned_project(db, project_id, user)
    return user


router = APIRouter(dependencies=[Depends(_owner_check)])


class RegenerateTitlesRequest(BaseModel):
    limit: int | None = None  # 最多处理几章（None = 全部）
    only_missing: bool = True  # 只处理缺标题的
    chapter_nos: list[int] | None = None  # 限定特定章节号
    sample: bool = False  # 只看样例（不写库）


class TitleChange(BaseModel):
    chapter_no: int
    old_title: str | None
    new_title: str
    cost: float


class RegenerateTitlesResponse(BaseModel):
    processed: int
    updated: int
    total_cost_usd: float
    changes: list[TitleChange]
    sample: bool = False


@router.post("/projects/{project_id}/regenerate-titles", response_model=RegenerateTitlesResponse)
def regenerate_titles(
    project_id: str,
    payload: RegenerateTitlesRequest,
    db: Session = Depends(get_db),
) -> RegenerateTitlesResponse:
    from engine.agents.title_generator import generate_title_for_chapter

    log.info(
        "regenerate_titles: project=%s limit=%s only_missing=%s nos=%s sample=%s",
        project_id, payload.limit, payload.only_missing, payload.chapter_nos, payload.sample,
    )

    q = db.query(Chapter).filter_by(project_id=project_id)
    if payload.chapter_nos:
        q = q.filter(Chapter.chapter_no.in_(payload.chapter_nos))
    rows = q.order_by(Chapter.chapter_no.asc()).all()

    # 过滤：跳过已有像样标题的（only_missing=True）
    def has_meaningful_title(t: str | None) -> bool:
        """判定标题是否「真正像标题」vs 「机械派生占位」。

        判定为 NOT meaningful（需要 LLM 重生成）的：
          - 完全空 / "（无标题）" / "未命名章节"
          - 占位「第N章」无 · 后缀
          - 含 [待修订]
          - 含「·发展·第N章：推进剧情」placeholder 痕迹
          - 超长（> 20 字）— 多半是机械从内容首句截的，不是真标题
        """
        if not t or not t.strip():
            return False
        s = t.strip()
        # 占位标题「第N章」（无 · 后缀）
        if re.match(r"^第\d+章$", s):
            return False
        # 待修订标记
        if "[待修订]" in s:
            return False
        # 兜底占位
        if s == "（无标题）" or s == "未命名章节":
            return False
        # 老 placeholder 痕迹
        if "推进剧情" in s:
            return False
        # 超长（机械从内容首句截的，> 20 字都不像标题）
        # 例：第270章·周一中午十二点四十七分（13 字）OK
        # 例：第270章·电脑屏幕的蓝光打在陆承脸上，三百一十七行转账记录（30+字）不行
        if len(s) > 20:
            return False
        return True

    if payload.only_missing:
        rows = [r for r in rows if not has_meaningful_title(r.title)]

    if payload.limit:
        rows = rows[: payload.limit]

    changes: list[TitleChange] = []
    total_cost = 0.0
    updated_count = 0
    import time as _time

    for row in rows:
        old_title = row.title
        content = row.content or ""
        meta = {"chapter_role": "发展"}
        # 退避重试：MiniMax-M3 速率限制（429）→ sleep 后重试
        new_title = ""
        cost = 0.0
        for attempt in range(4):
            try:
                new_title, cost = generate_title_for_chapter(
                    chapter_no=row.chapter_no,
                    content=content,
                    meta=meta,
                )
                break
            except Exception as e:
                log.warning("attempt %d failed for ch%d: %s", attempt + 1, row.chapter_no, e)
                if attempt < 3:
                    _time.sleep(2 ** attempt)  # 1, 2, 4 秒退避
                else:
                    log.exception("generate_title_for_chapter failed ch=%d after retries", row.chapter_no)

        total_cost += cost
        if not new_title or new_title == old_title:
            continue

        changes.append(TitleChange(
            chapter_no=row.chapter_no,
            old_title=old_title,
            new_title=new_title,
            cost=cost,
        ))

        if not payload.sample:
            row.title = f"第{row.chapter_no}章·{new_title}"
            updated_count += 1

        # 每章之间 sleep 200ms 避免触发 429
        _time.sleep(0.2)

    if not payload.sample:
        db.commit()
        log.info("regenerate_titles: updated %d chapters, total $%.4f", updated_count, total_cost)

    return RegenerateTitlesResponse(
        processed=len(rows),
        updated=updated_count,
        total_cost_usd=round(total_cost, 4),
        changes=changes,
        sample=payload.sample,
    )
