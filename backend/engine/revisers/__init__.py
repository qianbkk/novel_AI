"""设定回流 reviser 框架。

背景（docs/drafts/architecture-roadmap-2026-07-27.md §A2）：
正文在演化，但 `setting_package.json` 和 `arc_plans` 停在第 0 章。几百章后
必然对不上 L2 已确立的事实。本框架给"设定修订"一个可扩展的入口：
- 任何 reviser 实现 `propose(memory, setting, state) -> list[Revision]`
- 框架负责：注册 / 校验 / 证据闸门 / human_pending 落点 / 重复幂等 / 异常降级
- 本轮只实现 character_state_reviser，后续加势力/地图/大纲只是加实现
- **不直接写 setting**：提案进 human_pending，走既有人机协作通道

evidences 必须指回 L2 具体事实或章节号——这是防漂移的关键闸门。
没有 evidence 的提案一律丢弃（哪怕看似合理）。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable

from .. import memory as _memory  # noqa: F401  -- 占位，方便调用方一起 import

_log = logging.getLogger("novel_ai.engine.revisers")

VALID_TARGETS = ("setting", "arc_plan")


@dataclass(frozen=True)
class Revision:
    """一次修订提案。frozen → 不可变，方便做幂等 hash。

    target ∈ VALID_TARGETS：被拒收的 target 在 run_revisers 里丢弃。
    path   字符串寻址（"key_characters[0].description"）——简洁，不引
    JSONPath 库；后续若需要复杂路径再升级。
    evidence **必须**非空，否则被丢弃（§A2 验收点 2）。
    """
    target: str
    path: str
    current: object
    proposed: object
    evidence: str
    confidence: float = 1.0

    def __iter__(self):
        # 测试用 set(revision) == {...} 期望返回字段集合 —— 让 Revision
        # 同时支持 dict-style 迭代与 dataclass 字段访问。代价是不能
        # 直接 enumerate；测试里只对 Revision 做 set()/key 比对。
        return iter(asdict(self).keys())


# 注册表（顺序敏感：先注册先跑；多次调用 run_revisers 之间不重置，
# 因为测试场景里 `_reset_for_test` 显式清空）
_REVISERS: dict[str, Callable] = {}
# 已落到 human_pending 的提案 hash，用于幂等（同一 L2 状态不重复提案）
_PROPOSED: set[tuple[str, str, str]] = set()


def register_reviser(name: str, fn: Callable) -> None:
    """注册一个 reviser。同名重复注册会被忽略（保留第一个）——避免测试污染。

    fn 签名：fn(memory: dict, setting: dict, state: dict) -> Iterable[Revision]
    fn 抛任何异常都会被 run_revisers 捕获并降级（不阻断本弧写作）。
    """
    if name in _REVISERS:
        return
    _REVISERS[name] = fn


def _reset_for_test() -> None:
    """仅供测试使用：清空注册表和幂等缓存，并重新自动注册内置 reviser。"""
    _REVISERS.clear()
    _PROPOSED.clear()
    _auto_register_builtins()


def _auto_register_builtins() -> None:
    """第一次 import 时自动注册内置 reviser（character_state 等）。
    测试 fixture 会调用 _reset_for_test → 重新注册，确保跨测试一致。"""
    try:
        from . import character_state as _cs
        if "character_state_reviser" not in _REVISERS:
            _REVISERS["character_state_reviser"] = _cs.character_state_reviser
    except Exception as e:
        _log.warning("内置 reviser 注册失败: %s", e)


_auto_register_builtins()


def _evidence_ok(rev: Revision) -> bool:
    return isinstance(rev.evidence, str) and rev.evidence.strip() != ""


def _target_ok(rev: Revision) -> bool:
    return rev.target in VALID_TARGETS


def _idempotency_key(rev: Revision) -> tuple[str, str, str]:
    """幂等键：(target, path, evidence) —— 同一目标 + 同一路径 + 同一证据
    视为同一条提案，不重复入 human_pending。"""
    return (rev.target, rev.path, rev.evidence.strip())


def run_revisers(memory: dict, setting: dict, state: dict) -> list[Revision]:
    """跑所有注册过的 reviser，把合法 Revision 写入 state["human_pending"]。

    不修改 setting / arc_plans；不持久化到磁盘（orchestrator 负责）。
    异常隔离：单个 reviser 抛错 → 记 warning、降级为空、继续下一个。
    """
    pending = state.setdefault("human_pending", [])
    kept: list[Revision] = []

    for name, fn in _REVISERS.items():
        try:
            revisions = list(fn(memory, setting, state) or [])
        except Exception as e:
            # 故障要响亮（CLAUDE.md）：warning + 降级为空，不阻断本弧写作
            _log.warning("reviser %s failed; 降级为无提案: %s", name, e)
            continue

        for rev in revisions:
            if not isinstance(rev, Revision):
                _log.warning("reviser %s 返了非 Revision：%r", name, rev)
                continue
            if not _target_ok(rev):
                _log.warning("reviser %s 提案 target=%r 越界，丢弃", name, rev.target)
                continue
            if not _evidence_ok(rev):
                _log.warning("reviser %s 提案无 evidence，丢弃（防漂移闸门）", name)
                continue
            key = _idempotency_key(rev)
            if key in _PROPOSED:
                continue
            _PROPOSED.add(key)
            kept.append(rev)
            pending.append({
                "task_id": f"rev_{name}_{len(pending)}",
                "task_type": "confirm_revision",
                "description": f"{rev.target} · {rev.path}",
                "payload": {
                    "target": rev.target,
                    "path": rev.path,
                    "current": rev.current,
                    "proposed": rev.proposed,
                    "evidence": rev.evidence,
                    "confidence": rev.confidence,
                    "reviser": name,
                },
                "created_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
                "priority": "recommended",
            })

    return kept