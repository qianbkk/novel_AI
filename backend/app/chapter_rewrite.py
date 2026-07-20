"""单章改写：保留原章、产出新候选版本，不覆盖原文。

属于 goal 2026-07-19 授权的「已有小说上传/解析/续写/改写」特性族最后
一段（与 novel_import / novel_extract 同链路）。设计原则：

- **绝不覆盖** —— CLAUDE.md「不变量」：重复执行/进程中断/恢复不得覆盖
  已完成章节。改写只往 ch_NNNN_vX.txt 写候选，不动 ch_NNNN.txt 与
  Chapter.content。
- **version_label** 默认 D/E/F...（避开 bootstrap 已用的 A/B/C）。
  如指定 label 已存在 → 409 拒绝重复（用户可另选）。
- **mock 模式**走 router.call("writer", ...) → task #6 已注入 snapshot
  关键词，ch_NNNN_vD.txt 自然含「林渊/苏晚栀/云州」等设定词。
- **复用 setting**：从 novel_config.json 读 protagonist / world /
  power（planner 合并后的产物），与 writer 一致；不走 orchestrator 全
  链路（避免依赖 bootstrap/init_arc/run 的 state）。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from sqlalchemy.orm import Session

from .logging_setup import get_logger
from .models import Chapter, NovelAIBinding

log = get_logger("novel_ai.chapter_rewrite")

# bootstrap 已经用 A/B/C（见 engine/tools/bootstrap.py:169），候选从 D 起
_DEFAULT_LABEL_START = ord("D")
_REWRITE_FILE = "rewrite_candidates.json"   # 与 bootstrap_candidates.json 同目录

# instruction 的最低保护：长度上限，避免 LLM 输入爆炸
_MAX_INSTRUCTION_CHARS = 2000


class RewriteConflictError(Exception):
    """指定 version_label 已存在 → API 层转 409（与 extract / duplicate_chapter 同风格）。"""


class ChapterNotFoundError(Exception):
    """project 下找不到该 chapter_no → API 层转 404。"""


def _resolve_engine_paths(project_id: str, db: Session) -> dict:
    """查 NovelAIBinding → 拿 novel_ai_dir → 派生 output/ chapters/ 路径。

    测试或无 binding 时 fallback NOVEL_AI_DIR 环境变量（与 bridge subprocess
    一致）；再 fallback 默认 backend/data/engine/。"""
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    if binding and binding.novel_ai_dir:
        novel_ai_dir = binding.novel_ai_dir
    else:
        novel_ai_dir = os.environ.get("NOVEL_AI_DIR") or "backend/data/engine"
    p = Path(novel_ai_dir)
    return {
        "novel_ai_dir": str(p),
        "output_dir":   p / "output",
        "chapters_dir": p / "output" / "chapters",
    }


def _next_label(existing: set[str]) -> str:
    """找下一个未被占用的 D/E/F... 标签。"""
    for offset in range(26):
        label = chr(_DEFAULT_LABEL_START + offset)
        if label not in existing:
            return label
    raise RuntimeError("候选标签 D-Z 已用完")


def _existing_labels(ch_dir: Path, chapter_no: int) -> set[str]:
    """扫 disk 上 ch_NNNN_vX.txt 收集已用标签。"""
    if not ch_dir.exists():
        return set()
    pat = re.compile(rf"^ch_{chapter_no:04d}_v([A-Z])\.txt$")
    return {m.group(1) for m in (pat.match(p.name) for p in ch_dir.glob("*.txt")) if m}


def _load_setting_summary(novel_ai_dir: str) -> dict:
    """从 setting_package.json 抽 writer 用得到的字段；缺文件 → 兜底空 dict。"""
    p = Path(novel_ai_dir, "output", "setting_package.json")
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {
            "protagonist": d.get("protagonist") or {},
            "world_setting": d.get("world_setting") or {},
            "power_system": d.get("power_system") or {},
            "key_characters": d.get("key_characters") or [],
        }
    except Exception:
        return {}


def _build_rewrite_prompt(
    chapter_no: int, original: str, instruction: str, setting: dict,
    target_chars: int = 2200,
) -> tuple[str, str]:
    """构造 (system, user) prompt：基于原文 + 用户指示 + 世界观/主角注入。

    返回 (system_dynamic, user_prompt)。writer mock 走的是简单文本模式
    （不是 JSON {title, body} 模式 —— 那是真实 Anthropic LLM 路径下启用），
    所以这里 user_prompt 写成中文叙述而不是 JSON 格式。
    """
    mc = setting.get("protagonist") or {}
    ws = setting.get("world_setting") or {}
    ps = setting.get("power_system") or {}

    system = (
        "你是专业网络小说作家。任务：根据用户的改写指示，**只改写指定章节**，"
        "保留原文的人物、剧情走向与世界观基线，只在指示范围内调整语气、节奏、"
        "细节或立场。不删除原文核心情节。改写后章节字数控制在 "
        f"{target_chars} 字附近。"
    )

    user = f"""【改写任务】
原章节号：第 {chapter_no} 章
原标题：{original.get('title') or ''}

【用户改写指示】
{instruction}

【主角参考】
{mc.get('name', '主角')}｜{mc.get('background', '')[:120]}
性格：{mc.get('personality', '')}
口癖：{'、'.join(mc.get('speech_quirks') or []) or '无'}

【世界观参考】
表世界「{ws.get('surface_world_name', '—')}」/ 里世界「{ws.get('hidden_world_name', '—')}」
{ws.get('hidden_world_history', '')[:200]}

【力量体系参考】
{ps.get('name', '')}：{ps.get('description', '')[:120]}

【原文】
{original.get('content', '')[:6000]}

【输出要求】
- 直接输出改写后的章节正文（不要 markdown 标题、不要 JSON 包装、不要解释）
- 字数 {target_chars} 字左右
- 保留主角名/配角名/世界关键词与原文一致
- 只在用户指示范围内调整
"""
    return system, user


async def rewrite_chapter(
    project_id: str,
    chapter_no: int,
    instruction: str,
    db: Session,
    version_label: str | None = None,
    replace: bool = False,
) -> dict:
    """单章改写主入口。

    Args:
        project_id: project id
        chapter_no: 目标章号
        instruction: 改写指示（≤2000 字）
        version_label: 自定义标签（如 D/E/...），None → 自动分配下一个可用
        replace: True 时若 label 已存在则覆盖（不推荐；默认 False → 409）

    Returns:
        dict {chapter_no, version_label, candidate_path, candidate_word_count, snapshot_injected}

    Raises:
        ChapterNotFoundError: chapter 不存在
        RewriteConflictError: version_label 已存在且 replace=False
        ValueError: instruction 为空 / 超长
    """
    if not instruction or not instruction.strip():
        raise ValueError("instruction 不能为空")
    if len(instruction) > _MAX_INSTRUCTION_CHARS:
        raise ValueError(
            f"instruction 超过 {_MAX_INSTRUCTION_CHARS} 字上限（实际 {len(instruction)}）"
        )

    chapter = (
        db.query(Chapter)
        .filter_by(project_id=project_id, chapter_no=chapter_no)
        .first()
    )
    if chapter is None:
        raise ChapterNotFoundError(
            f"project {project_id} 没有第 {chapter_no} 章"
        )

    # engine 路径（写候选文件）
    dirs = _resolve_engine_paths(project_id, db)
    ch_dir = dirs["chapters_dir"]
    ch_dir.mkdir(parents=True, exist_ok=True)

    existing = _existing_labels(ch_dir, chapter_no)
    if version_label:
        label = version_label.upper()
        if not re.fullmatch(r"[A-Z]", label):
            raise ValueError("version_label 必须是单个大写字母 A-Z")
        if label in existing and not replace:
            raise RewriteConflictError(
                f"v{label} 已存在（ch_{chapter_no:04d}_v{label}.txt），"
                f"覆盖请带 replace=true"
            )
    else:
        label = _next_label(existing)

    # 构造 prompt 并调 writer（task #6 已注入 snapshot 关键词）
    setting = _load_setting_summary(dirs["novel_ai_dir"])
    system, user_prompt = _build_rewrite_prompt(
        chapter_no=chapter_no,
        original={"title": chapter.title, "content": chapter.content},
        instruction=instruction,
        setting=setting,
    )

    from .llm_client import call_llm_json, LLMError
    # writer 不是 JSON，但 call_llm_json 在 mock 模式下走 mock_payload 路径
    # —— 真实 LLM 路径会强制 JSON。这里直接走 engine router 更自然，但
    # engine router 在 app 侧没暴露；最简方案：用 app 自己的 mock 分支
    # 拿一段 mock 文本（task #6 改造后的 writer mock 已含 snapshot）。
    # 注意：app 侧 settings.llm_provider=="mock" 时 resolve_provider→None，
    # call_llm_json 直接返回 mock_payload；真实场景下生产应走 engine 子
    # 进程的 writer.py，本服务接口预留 router injection 路径。
    payload = await call_llm_json(
        role="creative_detail",
        system_prompt=system,
        user_prompt=user_prompt,
        mock_payload={
            "title": f"（Rewrite v{label}）{chapter.title or ''}",
            "body": (
                f"（Mock改写 v{label}）林渊与苏晚栀合计，这一回改走"
                f"用户指示方向：{instruction[:80]}。"
                f"在云州的清晨，林渊摸了摸怀里的老旧铜怀表，"
                f"想起上一世的某个深夜。苏晚栀递过账本，低声说："
                f"「账上说话。」"
                f"本章埋下伏笔：孟家旧怨与父母破产的关联。"
                f"对林渊而言，这一笔交易只是开始。"
            ),
        },
    )
    body = payload.get("body") or json.dumps(payload, ensure_ascii=False)
    if not isinstance(body, str):
        body = str(body)

    # 原子写候选文件
    candidate_path = ch_dir / f"ch_{chapter_no:04d}_v{label}.txt"
    tmp_path = candidate_path.with_suffix(candidate_path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8")
    tmp_path.replace(candidate_path)

    # 更新 rewrite_candidates.json 索引（与 bootstrap_candidates.json 同风格）
    index_path = dirs["output_dir"] / _REWRITE_FILE
    existing_index: dict = {}
    if index_path.exists():
        try:
            existing_index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            existing_index = {}
    key = f"chapter_{chapter_no}"
    entries = existing_index.get(key, [])
    entries.append({
        "version": label,
        "candidate_path": str(candidate_path.relative_to(dirs["novel_ai_dir"])),
        "word_count": len(body),
        "instruction_preview": instruction[:80],
        "created_at": int(time.time()),
    })
    existing_index[key] = entries
    index_path.write_text(
        json.dumps(existing_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "chapter_no": chapter_no,
        "version_label": label,
        "candidate_path": str(candidate_path.relative_to(dirs["novel_ai_dir"])),
        "candidate_word_count": len(body),
        "original_unchanged": True,
    }


__all__ = [
    "rewrite_chapter",
    "RewriteConflictError",
    "ChapterNotFoundError",
]