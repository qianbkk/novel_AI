"""character_state_reviser — 角色状态回流（§A2 第一个 reviser）。

对照 setting.key_characters[*].description 与 L2 hot.character_states 里
稳定下来的状态，产出差异提案。

规则：
- 仅采纳 stable=True 的状态（unstable 状态可能在变化中，强行改设定反而
  制造漂移）
- 仅采纳有 chapter 字段作证据的状态（§A2 验收 2：evidence 必须可追溯）
- 仅对 setting 里**已有**的角色发提案（新角色不进 setting，不在
  reviser 职责范围 —— 那应该是世界构建阶段的活）

返回 Revision 时不直接写 setting；由 run_revisers 统一塞进 human_pending。
"""
from __future__ import annotations
from typing import Iterable

from . import Revision, VALID_TARGETS


def _index_setting_characters(setting: dict) -> list[tuple[int, dict]]:
    """setting.key_characters 列表 → [(index, char_dict), ...]。空 / 缺键容忍。"""
    chars = setting.get("key_characters") or []
    if not isinstance(chars, list):
        return []
    out: list[tuple[int, dict]] = []
    for i, c in enumerate(chars):
        if isinstance(c, dict):
            out.append((i, c))
    return out


def _stable_states(memory: dict) -> dict[str, dict]:
    """L2 hot.character_states → {角色名: {value, chapter, stable}}。
    仅留 stable=True 的；值或 chapter 缺失的丢弃。"""
    raw = (memory.get("hot") or {}).get("character_states") or {}
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for name, st in raw.items():
        if not isinstance(st, dict):
            continue
        if not st.get("stable"):
            continue
        chapter = st.get("chapter")
        value = st.get("value")
        if not isinstance(chapter, int):
            continue
        if value is None:
            continue
        out[str(name)] = {"value": str(value), "chapter": chapter}
    return out


def character_state_reviser(
    memory: dict, setting: dict, state: dict,
) -> Iterable[Revision]:
    """比较 L2 稳定状态与 setting.key_characters 描述，发差异提案。"""
    stable = _stable_states(memory)
    if not stable:
        return []

    proposals: list[Revision] = []
    for idx, char in _index_setting_characters(setting):
        name = char.get("name")
        if not isinstance(name, str) or name not in stable:
            continue
        new_value = stable[name]["value"]
        current = char.get("description")
        if current == new_value:
            continue  # 已对齐，无须提案
        proposals.append(Revision(
            target="setting",
            path=f"key_characters[{idx}].description",
            current=current,
            proposed=new_value,
            evidence=f"第{stable[name]['chapter']}章：{name} {new_value}",
            confidence=0.9,
        ))
    return proposals


__all__ = ["character_state_reviser"] + ["Revision"]  # 让 from .character_state import Revision 也可用