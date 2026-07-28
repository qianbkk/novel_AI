from __future__ import annotations

from pathlib import Path

from tests._test_db import isolated_test_db  # noqa: F401


def test_auditor_uses_project_binding(tmp_path):
    from app.database import SessionLocal
    from app.models import NovelAIBinding, Project
    from scripts.audit_project import resolve_project_paths

    project_id = "audit-binding-project"
    engine_root = tmp_path / "bound-engine"
    db = SessionLocal()
    try:
        db.add(Project(id=project_id, title="审计绑定", genre="玄幻", config_json={}))
        db.commit()
        db.add(NovelAIBinding(
            project_id=project_id,
            novel_ai_dir=str(engine_root),
            novel_id=project_id,
        ))
        db.commit()
        setting, chapters = resolve_project_paths(project_id, db)
        assert setting == engine_root / "output" / "setting_package.json"
        assert chapters == engine_root / "output" / "chapters"
    finally:
        db.close()


def test_auditor_default_path_is_absolute():
    from app.database import SessionLocal
    from scripts.audit_project import DEFAULT_ENGINE_ROOT, resolve_project_paths

    db = SessionLocal()
    try:
        setting, chapters = resolve_project_paths("missing-project", db)
        assert DEFAULT_ENGINE_ROOT.is_absolute()
        assert setting == DEFAULT_ENGINE_ROOT / "output" / "setting_package.json"
        assert chapters == DEFAULT_ENGINE_ROOT / "output" / "chapters"
    finally:
        db.close()


def test_auditor_chapter_checks_only_bound_directory(tmp_path):
    from app.database import SessionLocal
    from app.models import Chapter, ChapterCharacter, Character, Project
    from scripts.audit_project import Auditor, audit_chapters

    bound = tmp_path / "bound" / "output" / "chapters"
    other = tmp_path / "other" / "output" / "chapters"
    bound.mkdir(parents=True)
    other.mkdir(parents=True)
    (bound / "ch_0001.txt").write_text("真正的正文开场。", encoding="utf-8")
    (other / "ch_0001.txt").write_text("第1章 错误重复标题", encoding="utf-8")

    db = SessionLocal()
    try:
        db.add(Project(id="p1", title="路径审计", genre="玄幻", config_json={}))
        db.commit()
        character = Character(project_id="p1", name="林渊", role="主角")
        chapter = Chapter(
            id="ch1", project_id="p1", chapter_no=1, title="第1章·开局",
            content="林渊真正的正文开场。" * 200,
            summary="开局", ai_assist_level="ai_assisted",
        )
        db.add_all([character, chapter])
        db.flush()
        db.add(ChapterCharacter(chapter_id=chapter.id, character_id=character.id))
        db.commit()

        auditor = Auditor("p1", chapters_dir=bound)
        audit_chapters(auditor, db)
        assert not any("首行" in warning for warning in auditor.warnings)
    finally:
        db.close()
