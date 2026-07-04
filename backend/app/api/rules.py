"""api/rules.py — 规则中心 (RuleCenter) 后端

端点：
  GET  /projects/{project_id}/rules                  读取项目规则配置
  PUT  /projects/{project_id}/rules                  更新项目规则配置
  POST /projects/{project_id}/rules/post-process     三个后处理工具（logic/venom/deai）

后处理实现思路：
  - logic: 用 LLM 检查章节的世界立法一致性（轻度调用，1 次）
  - venom: 同 logic，但 prompt 调成"严苛毒舌"语气（找出不合理处）
  - deai:  用 LLM 重写一遍，调成"去除 AI 痕迹"的语气；给出 diff 摘要

所有 LLM 调用走 backend.engine.llm_router 的 active router
（需要 graph 已 build_project_graph 过一次才会注册）。
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Chapter, Project, RuleConfig
from ..schemas import (
    PostProcessRequest,
    PostProcessResult,
    RuleConfigOut,
    RuleConfigUpsert,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["rules"])


VALID_TOOLS = {"logic", "venom", "deai"}
VALID_STYLES = {"webnovel", "literary", "wuxia"}


def _ensure_config(db: Session, project_id: str) -> RuleConfig:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    cfg = db.query(RuleConfig).filter_by(project_id=project_id).first()
    if not cfg:
        cfg = RuleConfig(project_id=project_id)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _serialize(cfg: RuleConfig) -> RuleConfigOut:
    return RuleConfigOut(
        project_id=cfg.project_id,
        style=cfg.style or "webnovel",
        taboos=list(cfg.taboos_json or []),
        template=cfg.template or "run.章节撰写",
        extra=dict(cfg.extra_json or {}),
        updated_at=cfg.updated_at,
    )


@router.get("/rules", response_model=RuleConfigOut)
def get_rules(project_id: str, db: Session = Depends(get_db)):
    cfg = _ensure_config(db, project_id)
    return _serialize(cfg)


@router.put("/rules", response_model=RuleConfigOut)
def put_rules(project_id: str, payload: RuleConfigUpsert,
              db: Session = Depends(get_db)):
    cfg = _ensure_config(db, project_id)
    if payload.style is not None:
        if payload.style not in VALID_STYLES:
            raise HTTPException(400, f"style must be one of {sorted(VALID_STYLES)}")
        cfg.style = payload.style
    if payload.taboos is not None:
        # 去重 + 保留顺序
        seen: set = set()
        cleaned: list[str] = []
        for t in payload.taboos:
            if not isinstance(t, str):
                continue
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                cleaned.append(t)
        cfg.taboos_json = cleaned
    if payload.template is not None:
        cfg.template = payload.template
    if payload.extra is not None:
        cfg.extra_json = payload.extra
    cfg.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cfg)
    return _serialize(cfg)


# ══════════════════════════════════════════
# 后处理工具
# ══════════════════════════════════════════
def _load_chapter_text(db: Session, project_id: str,
                       chapter_no: Optional[int]) -> tuple[int, str]:
    """Resolve (chapter_no, text) — pick latest if chapter_no not given."""
    q = db.query(Chapter).filter_by(project_id=project_id)
    if chapter_no is not None:
        ch = q.filter_by(chapter_no=chapter_no).first()
        if not ch:
            raise HTTPException(404, f"chapter {chapter_no} not found")
        return ch.chapter_no, (ch.content or "")
    ch = q.order_by(Chapter.chapter_no.desc()).first()
    if not ch:
        raise HTTPException(404, "no chapters yet")
    return ch.chapter_no, (ch.content or "")


def _style_hint(style: Optional[str]) -> str:
    s = style or "webnovel"
    return {
        "webnovel": "网文轻快：节奏紧凑、爽点密集、对白口语化",
        "literary": "文学正剧：克制笔法、留白充分、避免套路化",
        "wuxia": "武侠古风：半文半白、招式诗化、江湖气",
    }.get(s, "通用文风")


def _llm_call_for_postprocess(tool: str, style: Optional[str],
                              taboos: Optional[list[str]],
                              chapter_text: str) -> tuple[str, float]:
    """Single LLM call per post-process. Returns (raw_response, cost_usd).
    Falls back to a deterministic stub if no router is active."""
    style_hint = _style_hint(style)
    taboo_list = "\n".join(f"  - {t}" for t in (taboos or [])) or "  （无）"
    style_note = f"参考文风：{style_hint}。" if tool != "logic" else ""

    if tool == "logic":
        system = "你是逻辑评估 AI。检查章节的世界立法一致性、伏笔合规、时间线合理性。"
        user = (
            f"目标文风：{style_hint}\n"
            f"禁忌词清单（不应出现）：\n{taboo_list}\n\n"
            f"请评估以下章节（先给总分 1-10，再列 3-5 条具体问题）：\n\n"
            f"{chapter_text[:4000]}"
        )
        agent = "checker_main"
    elif tool == "venom":
        system = (
            "你是一位极度严苛的文学编辑，扮演'毒舌'角色。"
            "用犀利尖锐的语言找出文中不合理、逻辑硬伤、AI 痕迹、可删减的废话。"
            "即便章节写得不错也要挑剔——目标是帮作者改到更好。"
        )
        user = (
            f"参考文风：{style_hint}\n"
            f"禁忌词（出现了就点名）：\n{taboo_list}\n\n"
            f"请用毒舌口吻审视以下章节（先给总分 1-10，再列 5-8 条毒舌吐槽）：\n\n"
            f"{chapter_text[:4000]}"
        )
        agent = "checker_cross1"
    elif tool == "deai":
        system = (
            "你是'去 AI 痕迹'编辑。"
            "扫描文中典型 AI 套话（不仅/不禁/心中一动/眼眸/蓦然/不由得/话音刚落 等），"
            "给出：1) 检测到的 AI 词频次；2) 机械化排比句；3) 改写建议（不要整章重写，只给片段替换方案）。"
        )
        user = (
            f"目标文风：{style_hint}\n"
            f"自定义禁忌词（也应回避）：\n{taboo_list}\n\n"
            f"请扫描以下章节的 AI 痕迹并给出去味方案：\n\n"
            f"{chapter_text[:4000]}"
        )
        agent = "rewriter"
    else:
        raise HTTPException(400, f"unknown tool: {tool}")

    # Lazy import to avoid circular deps / heavy startup cost
    # 迭代 #37: 之前 except Exception 返回占位文本（"[tool] LLM 调用失败..."）
    # 是 fake-pass 同型问题——前端收到占位 + cost=0，误以为"逻辑评估完成"
    # 实际 LLM 失败。改为 raise HTTPException(503) 让用户看到真实错误。
    from engine.llm_router import get_active_router
    from engine.llm.router import LLMRouter
    try:
        router = get_active_router()
        if router is None:
            router = LLMRouter()
        text, cost = router.call(
            agent_name=agent,
            system_prompt=system,
            user_prompt=user,
            max_tokens=1500,
            temperature=0.3 if tool != "venom" else 0.4,
        )
        return text, cost
    except Exception as exc:
        # 真实 LLM 失败 → 直接抛 503（service unavailable）+ 错误信息
        # 让用户 / 前端能区分"成功完成"和"LLM 不可用"
        raise HTTPException(
            status_code=503,
            detail=f"LLM 调用失败（{type(exc).__name__}）：{exc}。"
                   f"请检查后端 providers / role-assignments 配置，或"
                   f"设置 NOVEL_ENGINE_MOCK=1 走 mock provider。",
        ) from exc


def _parse_findings(raw: str) -> list[dict]:
    """Best-effort: split numbered/bulleted lines into structured findings."""
    findings: list[dict] = []
    if not raw:
        return findings
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        # detect leading number / bullet
        if s[:2] in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.") \
                or s[:3] in ("10.", "11.", "12.") \
                or s.startswith(("•", "-", "·", "【", "▸")):
            findings.append({"line": s})
    # 兜底：整段作为一个 finding
    if not findings:
        findings.append({"line": raw[:600]})
    return findings


def _extract_score(raw: str) -> Optional[float]:
    """Best-effort score extraction: 'X.X分' or 'X.X/10'."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:分|/10|/ 10)", raw)
    return float(m.group(1)) if m else None


@router.post("/rules/post-process", response_model=PostProcessResult)
def post_process(project_id: str, payload: PostProcessRequest,
                 db: Session = Depends(get_db)):
    if payload.tool not in VALID_TOOLS:
        raise HTTPException(400, f"tool must be one of {sorted(VALID_TOOLS)}")

    # 确保项目存在
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    # 取章节正文
    chapter_no, text = _load_chapter_text(db, project_id, payload.chapter_no)
    if not text:
        raise HTTPException(400, f"chapter {chapter_no} has no content")

    # 取项目规则
    cfg = _ensure_config(db, project_id)
    style = payload.style or cfg.style
    taboos = payload.taboos if payload.taboos is not None else list(cfg.taboos_json or [])

    raw, cost = _llm_call_for_postprocess(payload.tool, style, taboos, text)

    summary = {
        "logic": "逻辑评估完成",
        "venom": "毒舌查漏完成",
        "deai":  "去 AI 痕迹扫描完成",
    }.get(payload.tool, "完成")

    return PostProcessResult(
        tool=payload.tool,
        chapter_no=chapter_no,
        summary=summary,
        findings=_parse_findings(raw),
        score=_extract_score(raw),
        cost_usd=cost,
        generated_at=datetime.now(timezone.utc),
    )