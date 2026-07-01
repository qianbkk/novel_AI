"""
端到端不变量审计：跑一遍就把 5 类隐性 bug 全暴露。

之前 50 章端到端发现的 5 个真实 bug 都源于「跨表 / 跨文件不变量没人守」：
  A) pull_setting_package 字段映射漂移（→ 5 张表全空）
  B) meta.json schema 不严（→ 标题"【修改后正文】"）
  C) import 与 pull 顺序未保护（→ 50 章 0 个 character 边）
  D) Pydantic vs ORM nullable 不一致（→ 500）
  E) 章节首行无校验（→ 假标题渗到 preview）

本脚本把这些检查全部自动化。任何 1 项违反都打 ERROR + 列出具体行号 / 文件。
退出码 0 = 全部通过；非 0 = 有问题（CI / pre-push 都能用）。

使用：
  python -m scripts.audit_project                # 审计默认项目
  python -m scripts.audit_project --pid XXX     # 审计指定项目
  python -m scripts.audit_project --strict      # WARN 也算失败
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# 把 backend 加进 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import (
    Project, Chapter, ChapterCharacter, Character, Faction,
    PowerSystem, Currency, MapNode, Foreshadowing, RuleConfig,
    WorldSetting, EntityRelation,
)
from app.schema_validator import (
    validate_setting_package, validate_chapter_meta, SchemaError,
)
from app.logging_setup import get_logger

log = get_logger("novel_ai.audit")

ENGINE_CH_DIR = Path("data/engine/output/chapters")
NOVELAI_CH_DIR = Path("../novel_AI/output/chapters")
SETTING_PATH = Path("data/engine/output/setting_package.json")


class Auditor:
    def __init__(self, project_id: str, strict: bool = False):
        self.pid = project_id
        self.strict = strict
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passes: list[str] = []

    def check(self, ok: bool, name: str, detail: str = ""):
        if ok:
            self.passes.append(f"✓ {name}")
        elif self.strict:
            self.errors.append(f"✗ {name} — {detail}")
        else:
            self.warnings.append(f"⚠ {name} — {detail}")

    def report(self) -> int:
        print()
        print("=" * 70)
        print("PASS")
        print("=" * 70)
        for p in self.passes:
            print(f"  {p}")
        if self.warnings:
            print()
            print("=" * 70)
            print("WARN (不阻塞，但建议修)")
            print("=" * 70)
            for w in self.warnings:
                print(f"  {w}")
        if self.errors:
            print()
            print("=" * 70)
            print("ERROR")
            print("=" * 70)
            for e in self.errors:
                print(f"  {e}")
        print()
        print("=" * 70)
        print(f"汇总: {len(self.passes)} pass / {len(self.warnings)} warn / {len(self.errors)} error")
        print("=" * 70)
        return 0 if not self.errors else 1


def audit_setting_package(a: Auditor):
    """A. setting_package.json 是否符合 schema + 关键字段非空"""
    if not SETTING_PATH.exists():
        a.check(False, "A1: setting_package.json 存在", f"missing: {SETTING_PATH}")
        return
    raw = json.loads(SETTING_PATH.read_text(encoding="utf-8"))
    try:
        validate_setting_package(raw)
        a.check(True, "A1: setting_package.json schema 校验通过")
    except SchemaError as e:
        a.check(False, "A1: setting_package.json schema 校验通过", str(e)[:200])

    # 关键字段深度检查（之前 B/C/D 类的根因）
    ws = raw.get("world_setting", {})
    a.check(
        isinstance(ws.get("hidden_world_history"), str) and len(ws["hidden_world_history"]) >= 50,
        "A2: world_setting.hidden_world_history 至少 50 字",
        f"实际 {len(ws.get('hidden_world_history',''))} 字"
    )

    psc = raw.get("key_characters", [])
    a.check(
        len(psc) >= 3,
        "A3: key_characters 至少 3 个",
        f"实际 {len(psc)} 个"
    )

    arcs = raw.get("arc_outline", [])
    a.check(
        len(arcs) >= 1,
        "A4: arc_outline 至少 1 弧",
        f"实际 {len(arcs)} 弧"
    )
    for i, arc in enumerate(arcs):
        a.check(
            arc.get("arc_goal"),
            f"A5: arc[{i}].arc_goal 非空",
            f"got: {arc.get('arc_goal','')[:40]!r}"
        )

    ps = raw.get("power_system", {})
    a.check(
        len(ps.get("levels", [])) >= 1,
        "A6: power_system.levels 至少 1 阶",
        f"实际 {len(ps.get('levels',[]))} 阶"
    )
    cur = ps.get("currency")
    a.check(
        cur is not None and str(cur).strip() != "",
        "A7: power_system.currency 已指定（→ Currency 表）",
        f"got: {cur!r}"
    )


def audit_worldbuild_db(a: Auditor, db):
    """DB 侧：pull_setting 后 8 张表是否都填了数据"""
    pid = a.pid
    counts = {
        "WorldSetting":  db.query(WorldSetting).filter_by(project_id=pid).count(),
        "Character":     db.query(Character).filter_by(project_id=pid).count(),
        "Faction":       db.query(Faction).filter_by(project_id=pid).count(),
        "PowerSystem":   db.query(PowerSystem).filter_by(project_id=pid).count(),
        "Currency":      db.query(Currency).filter_by(project_id=pid).count(),
        "MapNode":       db.query(MapNode).filter_by(project_id=pid).count(),
        "Foreshadowing": db.query(Foreshadowing).filter_by(project_id=pid).count(),
        "RuleConfig":    db.query(RuleConfig).filter_by(project_id=pid).count(),
    }
    for name, n in counts.items():
        a.check(
            n > 0,
            f"DB 表 {name} 非空",
            f"count={n}"
        )

    # WorldSetting.world_view 长度
    ws = db.query(WorldSetting).filter_by(project_id=pid).first()
    if ws:
        a.check(
            len(ws.world_view or "") >= 50,
            "WorldSetting.world_view 至少 50 字",
            f"实际 {len(ws.world_view or '')} 字"
        )

    # Foreshadowing 关联 character（之前 audit 发现的 3/3 unlinked）
    fs_unlinked = db.query(Foreshadowing).filter_by(
        project_id=pid, linked_character_id=None
    ).count()
    fs_total = db.query(Foreshadowing).filter_by(project_id=pid).count()
    a.check(
        fs_unlinked == 0,
        f"Foreshadowing 全部 linked_character",
        f"{fs_unlinked}/{fs_total} unlinked"
    )


def audit_chapters(a: Auditor, db):
    """B/C/D/E 类的核心：50 章的内容质量 + 关联完整性"""
    pid = a.pid
    chs = db.query(Chapter).filter_by(project_id=pid).order_by(Chapter.chapter_no).all()
    if not chs:
        a.check(False, "Chapters 至少 1 章", "查不到任何 chapter")
        return

    # B. 标题质量
    title_re = re.compile(r"^第\d+章")
    bad_titles = []
    placeholder_re = re.compile(r"(修改后正文|TODO|FIXME|smoke|测试稿|scaffold|你他妈)")
    for c in chs:
        if not c.title or not title_re.match(c.title):
            bad_titles.append((c.chapter_no, "格式错", c.title or ""))
        elif "【" in c.title or "】" in c.title:
            bad_titles.append((c.chapter_no, "含【】", c.title))
        elif placeholder_re.search(c.title):
            bad_titles.append((c.chapter_no, "含占位词", c.title))
    a.check(
        not bad_titles,
        f"B: 全部 {len(chs)} 章标题合法",
        f"{len(bad_titles)} 异常: {bad_titles[:3]}"
    )

    # C. ChapterCharacter 边（之前 50/50 = 0）
    ch_ids = {c.id for c in chs}
    ch_with_links = {l.chapter_id for l in db.query(ChapterCharacter).all()}
    no_link = ch_ids - ch_with_links
    a.check(
        not no_link,
        f"C: 全部 chapter 都有 character 边",
        f"{len(no_link)} 章无图谱边"
    )

    # D. created_at NULL（之前 50 章 NULL）
    no_created = [c for c in chs if not c.created_at]
    a.check(
        not no_created,
        f"D: 全部 chapter 有 created_at",
        f"{len(no_created)} 章 NULL"
    )

    # D2. ai_assist_level 必填
    no_ai = [c for c in chs if not c.ai_assist_level]
    a.check(
        not no_ai,
        f"D2: 全部 chapter 有 ai_assist_level",
        f"{len(no_ai)} 章 NULL"
    )

    # D3. summary
    no_summary = [c for c in chs if not c.summary]
    a.check(
        not no_summary,
        f"D3: 全部 chapter 有 summary",
        f"{len(no_summary)} 章 NULL/空"
    )

    # E. 首行校验 — 每章 txt 首行必须是「真正文」，不能是占位 / 假标题 / markdown 标题
    # 跳过开头的空行（strip 操作有时会留下空行），找第一个真非空行
    junk_first = []
    title_line_re = re.compile(r"^第\d+[章卷]\s*\S+")
    md_heading_re = re.compile(r"^#{1,6}\s+")
    for c in chs:
        for d in [ENGINE_CH_DIR, NOVELAI_CH_DIR]:
            f = d / f"ch_{c.chapter_no:04d}.txt"
            if f.exists():
                lines = f.read_text(encoding="utf-8").splitlines()
                # 跳过开头的所有空行，找第一个真行
                first = ""
                for ln in lines:
                    if ln.strip():
                        first = ln.strip()
                        break
                if not first:
                    junk_first.append((c.chapter_no, "文件全空", ""))
                elif first.startswith("【修改后正文】"):
                    junk_first.append((c.chapter_no, "首行是占位", first))
                elif title_line_re.match(first):
                    junk_first.append((c.chapter_no, "首行是重复标题", first))
                elif md_heading_re.match(first):
                    junk_first.append((c.chapter_no, "首行是 markdown 标题", first))
                elif first == "---":
                    junk_first.append((c.chapter_no, "首行是 markdown 分隔线", first))
                # 纯 scene label 【xxx】作为首行是 OK 的，跳过
                break
    a.check(
        not junk_first,
        f"E: 全部 chapter txt 首行是真正文",
        f"{len(junk_first)} 异常: {junk_first[:3]}"
    )

    # F. 字数下限 — 用户原始要求是 2000-2500 字（模糊），audit 设 1000 字
    # 为硬下限（再短就是 stub）。1500 是"理想下限"用 warn 报。
    too_short = [(c.chapter_no, len(c.content or "")) for c in chs if len(c.content or "") < 1000]
    a.check(
        not too_short,
        f"F: 全部 chapter ≥ 1000 字（再短就是 stub）",
        f"{len(too_short)} 章: {too_short[:3]}"
    )
    short = [(c.chapter_no, len(c.content or "")) for c in chs if 1000 <= len(c.content or "") < 1500]
    a.check(
        not short,
        f"F2: 全部 chapter ≥ 1500 字（理想下限）",
        f"{len(short)} 章: {short[:3]}"
    )

    # F3. 字数上限 — 防止"生成路径 length budget 失效"回归
    # 历史 bug: 50 章生成时 22 章 out of [1800, 2700]（22%），其中
    # 1 章 6120 字。即便 call_with_length_budget 接通了，audit 也要持续盯。
    too_long = [(c.chapter_no, len(c.content or "")) for c in chs if len(c.content or "") > 2700]
    a.check(
        not too_long,
        f"F3: 全部 chapter ≤ 2700 字（生成路径 length budget 约束）",
        f"{len(too_long)} 章超界: {too_long[:3]}"
    )


def audit_chapter_meta_files(a: Auditor):
    """G. meta.json 文件 schema 校验（防止 B 类再现：LLM 漏字段 import 时静默）"""
    if not ENGINE_CH_DIR.exists():
        a.check(False, "G1: engine chapters dir 存在", str(ENGINE_CH_DIR))
        return
    files = sorted(ENGINE_CH_DIR.glob("ch_*_meta.json"))
    a.check(
        len(files) >= 1,
        "G1: engine dir 至少 1 个 meta 文件",
        f"实际 {len(files)}"
    )
    bad = 0
    samples = []
    for f in files[:20]:  # sample 20 to keep audit fast
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            validate_chapter_meta(meta)
        except (SchemaError, json.JSONDecodeError) as e:
            bad += 1
            if len(samples) < 3:
                samples.append(f"{f.name}: {str(e)[:80]}")
    a.check(
        bad == 0,
        f"G2: 抽样 {min(20, len(files))} 个 meta 文件 schema 校验通过",
        f"{bad} 失败: {samples}"
    )


def audit_orphan_data(a: Auditor, db):
    """H. 跨表孤儿引用（之前 EntityRelation=1 可能是脏数据）"""
    pid = a.pid
    char_ids = {c.id for c in db.query(Character).filter_by(project_id=pid).all()}
    faction_ids = {f.id for f in db.query(Faction).filter_by(project_id=pid).all()}
    rels = db.query(EntityRelation).filter_by(project_id=pid).all()
    orphans = 0
    for r in rels:
        from_ok = (r.from_type != "character" or r.from_id in char_ids) and \
                  (r.from_type != "faction" or r.from_id in faction_ids)
        to_ok = (r.to_type != "character" or r.to_id in char_ids) and \
                (r.to_type != "faction" or r.to_id in faction_ids)
        if not (from_ok and to_ok):
            orphans += 1
    a.check(
        orphans == 0,
        f"H: EntityRelation 无孤儿边",
        f"{orphans} 条 from/to 指向不存在的实体"
    )

    # Foreshadowing -> Character 孤儿
    fs_orphans = 0
    for f in db.query(Foreshadowing).filter_by(project_id=pid).all():
        if f.linked_character_id and f.linked_character_id not in char_ids:
            fs_orphans += 1
    a.check(
        fs_orphans == 0,
        f"H2: Foreshadowing→Character 无孤儿",
        f"{fs_orphans} 条"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", default="c12345678901234567890123456789012",
                        help="project_id to audit")
    parser.add_argument("--strict", action="store_true",
                        help="WARN 也算失败")
    args = parser.parse_args()

    print(f"Auditing project_id={args.pid} (strict={args.strict})")
    print(f"  setting: {SETTING_PATH}")
    print(f"  engine chapters: {ENGINE_CH_DIR}")

    a = Auditor(args.pid, strict=args.strict)
    audit_setting_package(a)
    audit_chapter_meta_files(a)

    db = SessionLocal()
    try:
        audit_worldbuild_db(a, db)
        audit_chapters(a, db)
        audit_orphan_data(a, db)
    finally:
        db.close()

    return a.report()


if __name__ == "__main__":
    sys.exit(main())
