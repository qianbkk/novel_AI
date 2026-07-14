"""Generic utilities used by agents.

Migrated from novel_AI/utils.py. Provides:
  - parse_llm_json_response: best-effort JSON parse with default fallback
  - atomic_write_json: 原子写 JSON（先 .tmp + os.replace）
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from typing import Any

log = logging.getLogger("novel_ai.utils")


def strip_markdown_fence(resp: str | None) -> str | None:
    """脱掉 LLM 响应最外层 ```json ... ``` fence（去首尾空白后第一行是 ``` 时）。

    多个 agent（checker / tracker / outline / memory_manager）以前各自 inline 同样的
    剥 fence 代码（lines[1:] + lines[:-1] if 末位 ```）。这里集中一处。

    不试图解析内层 — 仅剥外层 fence。parse_llm_json_response 进一步做 JSON 解析。
    返回脱完 fence 后的字符串（无 fence 时也 strip + 同样返回，行为一致）。
    None / 空字符串 → 原样返回（None 透传，空串经 strip 仍空串）。
    """
    if not resp:
        return resp
    s = resp.strip()
    if not s.startswith("```"):
        return s  # 两个分支都 strip，保持返回值格式一致
    lines = s.split("\n")
    lines = lines[1:]  # drop 头部 ```json / ```
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def truncate_preserving_ends(
    text: str,
    *,
    head_chars: int = 1500,
    tail_chars: int = 2000,
    threshold: int = 4000,
    placeholder: str = "\n\n...【中段省略】...\n\n",
) -> str:
    """章节较长时保留头 + 尾，避免质检 / 状态抽取截掉弧高潮的尾段。

    单源：改阈值只一处，跨 agent 行为一致。
    调用方（Phase 5/8 fix）：checker / tracker 都满足 head+tail < threshold。

    前置条件 head+tail < threshold 违反时：log warning + 原样返回（fail-soft），
    避免未来新 caller 误用产出比原文更长的"截断"喂给 LLM。
    """
    if head_chars + tail_chars >= threshold:
        log.warning(
            "truncate_preserving_ends: head_chars(%d) + tail_chars(%d) >= threshold(%d)，"
            "「截断」会比原文更长——本次原样返回。",
            head_chars, tail_chars, threshold,
        )
        return text
    if len(text) <= threshold:
        return text
    return text[:head_chars] + placeholder + text[-tail_chars:]


def _coerce_type(parsed: Any, default: Any) -> Any:
    """类型保护：parse 出来的对象必须跟 default 类型一致。

    历史上（你独立验证）：tracker 等 agent 假设 parse 返回 dict，
    但 LLM 偶尔返回 list/None/str → 后续 `updates.get(...)` 抛
    `'list' object has no attribute 'get'`，60+ 章连续报错。

    修法（系统级）：如果类型不匹配，自动把 parsed 转成 default 的
    形状——dict 缺失就回 default、list 缺失就回 default。如果是 None
    而 default 是 dict，回 {}，list 回 []，str 回 ""。

    严格场景下（schema 强校验），agent 应该传入 TypedDict 或 Pydantic
    模型；这里只做"软保护"避免下游整个崩。

    default=None 是「哨兵值」语义：调用方想用 None 表示「parse 失败」
    而非「空 dict」，因此 default=None 时不做类型检查，直接返回 parsed
    （None 表示 parse 全部失败）。
    """
    if parsed is None:
        # 全部 parse 失败 → 根据 default 类型返回空值（fail-soft）
        # - summarizer.py 传 default=None → 走最后一行 return default，
        #   调用方有 if arc_summary is None 兜底
        # - 其他 agent 传 dict/list/str → 返回对应空值
        if isinstance(default, dict):
            return {}
        if isinstance(default, list):
            return []
        if isinstance(default, str):
            return ""
        return default
    # 哨兵：default=None → 不做类型检查，parsed 是什么就返回什么
    # （让调用方用 None 检测 parse 失败，iter #40 tracker 用此机制）
    if default is None:
        # 但 30 章实验发现：LLM 偶尔返回 list/str 而不是 dict（JSON shape
        # 漂移），下游 `updates.get(...)` 会报 "'list' object has no attribute
        # 'get'"。修法：default=None 的哨兵语义是「检测 parse 失败」，
        # 而 parse 成功的语义应是「拿到一个结构化对象」。非 dict 视为
        # parse 失败（返回 None），让下游走 meta 标记路径。
        if not isinstance(parsed, dict):
            log.warning(
                "parse_llm_json_response: default=None 但 parsed 非 dict"
                " (got %s) — 视为 parse 失败，返 None",
                type(parsed).__name__,
            )
            return None
        return parsed
    # 类型匹配 → 直接返回（dict / list / str 分别检查，因为 isinstance(dict, object) 不会混淆）
    if isinstance(default, dict) and isinstance(parsed, dict):
        return parsed
    if isinstance(default, list) and isinstance(parsed, list):
        return parsed
    if isinstance(default, str) and isinstance(parsed, str):
        return parsed
    # 类型不匹配 → 警告 + 回 default
    log.warning(
        "parse_llm_json_response: type mismatch (default=%s, got=%s) — falling back to default",
        type(default).__name__, type(parsed).__name__,
    )
    return default


def parse_llm_json_response(resp: str, default):
    """Best-effort JSON parse of an LLM response.

    Strips ```json ... ``` fences, regex-searches the first balanced JSON
    object/array, and returns the parsed value. Falls back to `default`
    on any failure (returns `default` as-is, including None).

    类型保护（参见 _coerce_type）：返回前会校验 parsed 是否跟 default
    同型，否则警告 + 退回 default。
    """
    if not resp:
        return default

    # Strip ``` fences — 复用 strip_markdown_fence（不再 inline fence 剥离）
    s = strip_markdown_fence(resp)
    if s is None:
        return default

    parsed: Any = None

    # Try direct parse
    try:
        parsed = json.loads(s)
    except Exception:
        pass

    # Try to find the first balanced JSON object/array
    if parsed is None:
        for opener, closer in (('{', '}'), ('[', ']')):
            start = s.find(opener)
            if start < 0:
                continue
            depth = 0
            for i in range(start, len(s)):
                ch = s[i]
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        candidate = s[start:i+1]
                        try:
                            parsed = json.loads(candidate)
                            break
                        except Exception:
                            break
            if parsed is not None:
                break

    # Try a forgiving cleanup (remove trailing commas in objects/arrays)
    if parsed is None:
        cleaned = re.sub(r",\s*([}\]])", r"\1", s)
        try:
            parsed = json.loads(cleaned)
        except Exception:
            pass

    # 全部失败 → default + log（迭代 #80：fake-pass 同型问题被点出）
    # 之前 3 个策略失败时静默 return default → caller 拿 default 不知道 LLM
    # 返回了垃圾。修法：log.warning 带 resp[:200] + strategy count 让运维
    # 看到「这次 LLM 返回不合法 JSON」的信号，但**仍 return default** 保证
    # pipeline 继续（行为不变）。
    if parsed is None:
        log.warning(
            "parse_llm_json_response: 3 个策略全失败，fallback 到 default "
            "(LLM 返回可能损坏 / 非 JSON)。resp[:200]=%r",
            (resp or "")[:200],
        )
        return default

    # 类型保护
    return _coerce_type(parsed, default)


# ════════════════════════════════════════════════════════════════════
# Atomic JSON write — 防止写一半被杀导致文件损坏
# ════════════════════════════════════════════════════════════════════
def atomic_write_json(path: str, data: Any) -> None:
    """原子写 JSON：先写 .tmp 再 os.replace，避免半写文件被下次读到。

    模式来自 engine.state.save_state，被 save_l2 / save_l5 复用，
    现在推广到所有需要写 JSON 到磁盘的地方（setting_package.json 等）。

    - 写 .tmp + flush + best-effort fsync
    - os.replace 重试 3 次（Windows 上并发 rename 可能 WinError 32）
    - 全部失败才抛

    进程被杀 / 写一半断电 → 老的完整 .json 保留，.tmp 可能是损坏的。
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            # Windows 上 fsync 不一定支持，best-effort
            pass
    last_exc: OSError | None = None
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return
        except OSError as e:
            last_exc = e
            time.sleep(0.05 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ════════════════════════════════════════════════════════════════════
# call_with_budget_with_retry — 写入路径字数控制的统一重试包装
# ════════════════════════════════════════════════════════════════════
def call_with_budget_with_retry(
    router,                          # backend.engine.llm.router.LLMRouter
    agent_name: str,
    system: str,
    user: str,
    target_chars: int,
    *,
    temperature: float = 0.82,
    tolerance: int = 200,
    max_continues: int = 2,
    sleep_seconds: float = 30.0,
    max_attempts: int = 2,          # 1 try + 1 retry
) -> tuple[str, float]:
    """统一的 length-budget 调用 + 网络抖动重试包装。

    之前 writer.py / rewriter.py 各自有一份几乎相同的 `_call_with_budget`
    （~30 行重复代码）。抽到这里共享。

    重试策略：
    - router._post_with_retry 已有 tenacity 3 次 retry，指数 1-10s（最多 30s）
    - 这里加 agent-level 兜底：max_attempts=2（1 try + 1 retry），间隔 sleep_seconds
    - 默认 30s sleep 是经验值（MiniMax 偶尔出现 30-60s 短暂不可用，再长用户等不及）
    - 全部失败 → 抛最后一次异常，让 orchestrator 走 escalate

    注：之前 writer.py 的 comment 说「3 次（每次 60s 内）」是错的——代码实际只跑 2 次。
    这次重写时修正：max_attempts 默认 2（与历史行为一致），如需 3 次可外部传参。
    """
    import httpx as _httpx
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return router.call_with_length_budget(
                agent_name=agent_name,
                system_prompt=system,
                user_prompt=user,
                target_chars=target_chars,
                tolerance=tolerance,
                temperature=temperature,
                max_continues=max_continues,
            )
        except (_httpx.TransportError, _httpx.HTTPStatusError, ConnectionError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                time.sleep(sleep_seconds)
    raise last_exc  # type: ignore[misc]