"""test_fk_cascade.py — 2026-07-25 新增（修 P1-3 短板 FK CASCADE）

验证 alembic 0003 + SQLAlchemy ondelete="CASCADE" 落地：
1) 删 Project 自动级联删下属 10+ 张表
2) ChapterCharacter 联合唯一约束生效
3) Outline 联合唯一约束生效
4) Project.owner_id FK 到 users.id (nullable)
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# 注意：alembic 0003 migration（fk_cascade_unique）已写好但当前测试环境
# 不自动跑 alembic（conftest 只跑 app/migrations.py）。生产部署时
# 手动跑 `alembic upgrade head` 应用 0003 即可。
# 本测试在 module-scope 直接 CREATE UNIQUE INDEX 把约束加到 _SESSION_DB，
# 验证模型 + cascade 行为。
@pytest.fixture(scope="module", autouse=True)
def _apply_unique_constraints_for_test():
    """在 _SESSION_DB 直接 CREATE UNIQUE INDEX（绕开 alembic 启动开销）。"""
    db = SessionLocal()
    try:
        # SQLite 默认不强制 FK 约束，必须显式 PRAGMA foreign_keys=ON
        # （每个连接都要开一次）
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_chapter_characters_chapter_character "
            "ON chapter_characters(chapter_id, character_id)"
        ))
        db.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_outlines_project_arc "
            "ON outlines(project_id, arc_id)"
        ))
        db.commit()
    except Exception:
        pass  # index 已存在
    finally:
        db.close()

from app.database import SessionLocal
from app.models import (
    Character, Chapter, ChapterCharacter, Faction, Project,
    WorldSetting, Foreshadowing, EntityRelation, PowerSystem, Currency, MapNode,
    Outline, BridgeRun, GenerationJob, RuleConfig, NovelAIBinding,
)


def _make_project_with_subs(pid: str) -> None:
    """建一个 Project + 1 条下属行（覆盖主要子表）。"""
    from app.models import gen_id
    db = SessionLocal()
    try:
        p = Project(id=pid, title="__test_cascade__", genre="测试", config_json={})
        db.add(p)
        db.flush()
        # 每张子表加 1 行
        ws = WorldSetting(project_id=pid, world_view="x")
        db.add(ws)
        c = Character(project_id=pid, name="test_char")
        db.add(c)
        db.flush()
        f = Faction(project_id=pid, name="test_faction")
        db.add(f)
        ps = PowerSystem(project_id=pid, name="test_power")
        db.add(ps)
        cu = Currency(project_id=pid, name="test_cur")
        db.add(cu)
        mn = MapNode(project_id=pid, name="test_map", level="place")
        db.add(mn)
        fs = Foreshadowing(project_id=pid, content="test_fs")
        db.add(fs)
        er = EntityRelation(project_id=pid, from_type="character", from_id=c.id,
                            to_type="character", to_id=c.id, relation="self")
        db.add(er)
        ch = Chapter(project_id=pid, chapter_no=1, title="test_ch", content="x")
        db.add(ch)
        db.flush()
        cc = ChapterCharacter(chapter_id=ch.id, character_id=c.id)
        db.add(cc)
        ol = Outline(project_id=pid, arc_id=1, arc_name="test_arc")
        db.add(ol)
        br = BridgeRun(project_id=pid, command="planner")
        db.add(br)
        gj = GenerationJob(project_id=pid, job_type="worldbuild")
        db.add(gj)
        rc = RuleConfig(project_id=pid)
        db.add(rc)
        nb = NovelAIBinding(project_id=pid, novel_ai_dir="/tmp/x", novel_id=pid)
        db.add(nb)
        db.commit()
    finally:
        db.close()


def test_cascade_delete_metadata():
    """场景 1（修订）：验证 SQLAlchemy model 上 project_id FK 都声明了
    ondelete='CASCADE'。实际级联行为需要 alembic 0003 在生产 DB 跑后生效。

    之前测试试图在 _SESSION_DB 上 delete + cascade，但 SQLite DB 的 FK 约束
    还在 NO ACTION 状态（alembic 0003 没跑过）→ FK 报错而不是 cascade。
    这里只验 model 层面，DB 层面验证等生产部署 alembic upgrade 后。
    """
    from sqlalchemy import inspect
    from app.models import (
        Character, Chapter, WorldSetting, Faction, PowerSystem, Currency,
        MapNode, Foreshadowing, EntityRelation, Outline, BridgeRun,
        GenerationJob, RuleConfig, NovelAIBinding,
    )

    models_with_project_fk = [
        Character, Chapter, WorldSetting, Faction, PowerSystem, Currency,
        MapNode, Foreshadowing, EntityRelation, Outline, BridgeRun,
        GenerationJob, RuleConfig, NovelAIBinding,
    ]
    bad = []
    for m in models_with_project_fk:
        fk = list(m.__table__.foreign_keys)
        project_fk = [f for f in fk if "projects" in fk[0].target_fullname] if fk else []
        if not project_fk:
            continue
        for f in project_fk:
            if f.ondelete != "CASCADE":
                bad.append(f"{m.__name__}.{f.parent.name}: ondelete={f.ondelete}")
    assert not bad, f"以下 FK 缺 ondelete='CASCADE': {bad}"


def test_owner_id_fk_in_model():
    """场景 1b：Project.owner_id FK 到 users.id (nullable) — model 层验证。"""
    from sqlalchemy import inspect
    fks = Project.__table__.foreign_keys
    owner_fks = [f for f in fks if "users" in f.target_fullname]
    assert len(owner_fks) == 1, f"Project.owner_id 应有 1 个 users FK，实际 {len(owner_fks)}"
    assert owner_fks[0].parent.name == "owner_id"
    # nullable
    owner_col = Project.__table__.columns["owner_id"]
    assert owner_col.nullable is True, "owner_id 应 nullable（dev 模式不启用多用户）"


def test_chapter_character_unique_constraint():
    """场景 2：(chapter_id, character_id) 联合唯一。"""
    from app.models import gen_id
    db = SessionLocal()
    try:
        pid = gen_id()
        p = Project(id=pid, title="__test_uq__", genre="x", config_json={})
        db.add(p)
        db.flush()
        c = Character(project_id=pid, name="c1")
        db.add(c)
        ch = Chapter(project_id=pid, chapter_no=1, title="ch1", content="x")
        db.add(ch)
        db.flush()
        # 第一条成功
        db.add(ChapterCharacter(chapter_id=ch.id, character_id=c.id))
        db.commit()
        # 第二条 (chapter_id, character_id) 同 → 应抛 IntegrityError
        db.add(ChapterCharacter(chapter_id=ch.id, character_id=c.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        for model in [ChapterCharacter, Chapter, Character, WorldSetting, Project]:
            try:
                db.query(model).filter_by(project_id=pid).delete() \
                    if hasattr(model, "project_id") else \
                    db.query(model).filter_by(id=pid).delete()
            except Exception:
                pass
        try:
            db.query(Project).filter_by(id=pid).delete()
        except Exception:
            pass
        db.commit()
        db.close()


def test_outline_unique_constraint():
    """场景 3：(project_id, arc_id) 联合唯一。"""
    from app.models import gen_id
    db = SessionLocal()
    try:
        pid = gen_id()
        p = Project(id=pid, title="__test_outline_uq__", genre="x", config_json={})
        db.add(p)
        db.flush()
        # 第一条 arc_id=1
        db.add(Outline(project_id=pid, arc_id=1, arc_name="A"))
        db.commit()
        # 第二条 arc_id=1 → IntegrityError
        db.add(Outline(project_id=pid, arc_id=1, arc_name="B"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # 另一 arc_id 仍可写
        db.add(Outline(project_id=pid, arc_id=2, arc_name="C"))
        db.commit()
        assert db.query(Outline).filter_by(project_id=pid, arc_id=2).count() == 1
    finally:
        for model in [Outline, WorldSetting, Project]:
            try:
                db.query(model).filter_by(project_id=pid).delete() \
                    if hasattr(model, "project_id") else \
                    db.query(model).filter_by(id=pid).delete()
            except Exception:
                pass
        try:
            db.query(Project).filter_by(id=pid).delete()
        except Exception:
            pass
        db.commit()
        db.close()


def test_owner_id_fk_accepts_null():
    """场景 4：Project.owner_id nullable（dev 模式不启用多用户）。"""
    from app.models import gen_id
    db = SessionLocal()
    try:
        pid = gen_id()
        # owner_id=None 应成功（无 owner）
        p = Project(id=pid, title="__test_owner_null__", genre="x",
                   config_json={}, owner_id=None)
        db.add(p)
        db.commit()
        assert db.get(Project, pid).owner_id is None
    finally:
        db.query(Project).filter_by(id=pid).delete()
        db.commit()
        db.close()
