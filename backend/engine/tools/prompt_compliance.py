"""prompt_compliance.py — writer prompt 指令遵循率测量（§A5 验收先决条件）

背景（docs/drafts/architecture-roadmap-2026-07-27.md §A5）：
writer prompt 有 24 个 `【】` 指令块。直觉上"块多了 = 遵循率下降"，但没测过
就无从判断改好还是改坏。本工具给"先测量"提供入口：
- build_chapter_constraints(task, setting, context) → Constraint 列表
- score_compliance(chapter_text, constraints) → 各项命中 / 漏命中报告

约束形态（可断言）：
- 必须包含角色：chapter_text 出现主角色名
- 必须回收伏笔：chapter_text 出现伏笔描述中的关键短语
- 不得新增未列出的角色：chapter_text 不出现 setting.key_characters 以外的
  "人名"（粗略：用常见姓氏模式）

本工具不动 writer prompt，只读 build_writer_prompt 输出 + 真章节正文，跑
N 章统计各项命中率。这是 §A5 唯一允许"先做"的部分；改 prompt 等基线
数据出来再做。
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Constraint:
    """可自动断言的一条硬约束。

    kind 决定检查方式：
      - "must_include": chapter_text 必须包含 phrase（substring / 任一 alias）
      - "must_must_recycle": chapter_text 必须包含伏笔的任一关键词
      - "must_not_new_character": chapter_text 不能出现 setting.key_characters
        之外的人名候选（粗略：姓氏 + 单字名的组合，且与禁止名单匹配）
    """
    kind: str
    description: str
    phrases: tuple[str, ...] = ()  # 必须包含的关键词列表（任一命中即满足）
    forbid: tuple[str, ...] = ()   # 必须不包含的字符串（任一命中即违反）


@dataclass
class ComplianceReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


# 常见中英文姓氏首字（用于"不得新增未列出角色"的粗略识别）
_NAME_SURNAMES = set("赵钱孙李周吴郑王冯陈楚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
_NAME_TITLES = set("公子姑娘少主长老宗主掌门大人前辈先生夫人")


def _looks_like_name(token: str) -> bool:
    """粗略判断 token 是不是人名候选。2-3 字汉字 + 首字是姓氏或称谓。"""
    if not token or not (2 <= len(token) <= 3):
        return False
    if not all('一' <= ch <= '鿿' for ch in token):
        return False
    return token[0] in _NAME_SURNAMES or token in _NAME_TITLES


def _extract_candidate_names(text: str) -> set[str]:
    """从正文中提取候选人物名（粗略，足够测"漏入新角色"）。

    仅作测量用，不参与生产逻辑。识别规则（按可靠性从高到低）：
    - 1) 「X」/『X』/"X" 等标识
    - 2) "X道：" / "X说、" 等说话前缀（中文网络小说主流）
    - 3) 句首 2-3 字作名字（"柳墨在旁边。" → "柳墨"），首字是姓氏

    命名型判断只起"提醒"作用，precision 不重要 —— 漏报比误报更危险，
    而误报的代价是 hint 一次（人类审核时忽略即可）。
    """
    out: set[str] = set()
    # 1) 引号/方头括号内的人名
    for m in re.finditer(r"[「『\"]([一-鿿]{2,3})[」』\"]", text):
        out.add(m.group(1))
    # 2) 说话前缀："X道" / "X说" —— 匹配 "柳墨在"，随后字为"在/说"等动词
    for m in re.finditer(
        r"([一-鿿]{2,3})(?:说道|问道|喊道|低声道|沉声道|喝道|道：|道,|道:|"
        r"说：|说,|说:|答道|答：|答,|在)",
        text,
    ):
        out.add(m.group(1))
    # 3) 句首 2-3 字作为名字（"柳墨在旁边。" → "柳墨"），首字必须是姓氏
    #    不要贪婪：只看句号/感叹号/问号/换行 后的前 2-3 字，且首字是姓氏
    for m in re.finditer(r"(?:^|[。！？!?\n])([一-鿿]{2,3})", text):
        token = m.group(1)
        if _looks_like_name(token):
            out.add(token)
    return out


def build_chapter_constraints(
    *, task: dict, setting: dict, context: dict,
    foreshadow_due: Iterable[dict] = (),
) -> list[Constraint]:
    """从 task / setting / context / foreshadow 派生本章节可自动断言的硬约束。

    foreshadow_due 应是 manager._foreshadow_target_chapter 产出的 [{desc, due_chapter, ...}]
    本工具只摘出 due_chapter <= 当前章节号的，即"该回收的"。
    """
    constraints: list[Constraint] = []

    # 1. 必须包含主角色
    main_chars = task.get("main_characters") or []
    if main_chars:
        constraints.append(Constraint(
            kind="must_include",
            description=f"本章必须包含主角色 {main_chars[0]}",
            phrases=tuple(str(c) for c in main_chars),
        ))

    # 2. 必须包含次要主角色
    for c in (main_chars[1:] if len(main_chars) > 1 else []):
        constraints.append(Constraint(
            kind="must_include",
            description=f"本章应出现次要角色 {c}",
            phrases=(str(c),),
        ))

    # 3. 必须回收即将到期伏笔
    due_phrases: list[str] = []
    for f in foreshadow_due:
        desc = (f.get("desc") or "").strip()
        if not desc:
            continue
        # 取前 4 字作为"识别词" —— 太长容易误判
        due_phrases.append(desc[:4])
    if due_phrases:
        constraints.append(Constraint(
            kind="must_must_recycle",
            description=f"本章应回收 {len(due_phrases)} 条伏笔",
            phrases=tuple(due_phrases),
        ))

    # 4. 不得新增未列出角色
    raw_chars = setting.get("key_characters") if isinstance(setting, dict) else None
    if not isinstance(raw_chars, list):
        raw_chars = []
    known = set(str(c) for c in main_chars) | set(
        (k.get("name") or "") for k in raw_chars if isinstance(k, dict)
    )
    known.discard("")
    # 常见误识别的角色代词/称谓不算新角色
    forbidden = {
        n for n in _extract_candidate_names_from_text(str(task))
        if n and n not in known
    }
    if forbidden:
        constraints.append(Constraint(
            kind="must_not_new_character",
            description=f"本章不得新增未列出角色 {sorted(forbidden)}",
            forbid=tuple(sorted(forbidden)),
        ))

    return constraints


def _extract_candidate_names_from_text(text: str) -> set[str]:
    """辅助：从给定文本（task 描述）里提前出现的人名作为"不得新增"集合。

    实际正文里会自然出现更多；这里只起提示作用，正文阶段的检测才是断言来源。
    """
    return _extract_candidate_names(text)


def score_compliance(chapter_text: str, constraints: list[Constraint]) -> ComplianceReport:
    """逐条断言约束，返回报告。"""
    report = ComplianceReport()
    for c in constraints:
        report.total += 1
        hit = False
        if c.kind == "must_include":
            hit = any(p and p in chapter_text for p in c.phrases)
        elif c.kind == "must_must_recycle":
            hit = any(p and p in chapter_text for p in c.phrases)
        elif c.kind == "must_not_new_character":
            hit = not any(p and p in chapter_text for p in c.forbid)
        else:
            hit = True  # unknown kind: 不判

        entry = {
            "kind": c.kind,
            "description": c.description,
            "hit": hit,
        }
        report.details.append(entry)
        if hit:
            report.passed += 1
        else:
            report.failed += 1
    return report


def names_introduced(chapter_text: str, known: set[str]) -> set[str]:
    """返回正文里出现、但 known 集合里没有的候选人名（用于"漏入新角色"诊断）。"""
    candidates = _extract_candidate_names(chapter_text)
    return {c for c in candidates if c not in known}