from __future__ import annotations

import json
from pathlib import Path


def _setting() -> dict:
    return {
        "title_candidates": ["星海余烬"],
        "genre": "科幻",
        "platform": "personal",
        "tagline": "失落舰队重返星海",
        "budget_limit_usd": 10,
        "protagonist": {
            "name": "沈岚",
            "age": 29,
            "background": "深空救援员",
            "personality": "冷静克制",
            "speech_quirks": ["先看数据"],
        },
        "key_characters": [{"name": "顾舟", "role": "领航员"}],
        "world_setting": {
            "surface_world_name": "远环殖民地",
            "hidden_world_history": "旧文明舰队失踪百年",
        },
        "power_system": {"name": "遗迹共鸣"},
        "golden_chapter_hooks": {},
        "arc_outline": [
            {
                "arc_id": 1,
                "arc_name": "归航",
                "arc_goal": "找到失踪舰队",
                "estimated_chapters": 12,
            }
        ],
    }


def test_bootstrap_prompts_are_setting_driven():
    from engine.tools.bootstrap import build_bootstrap_system, build_golden_tasks

    setting = _setting()
    tasks = build_golden_tasks(setting)
    system = build_bootstrap_system(setting)
    rendered = json.dumps(tasks, ensure_ascii=False) + system

    assert "星海余烬" in system
    assert "沈岚" in rendered
    assert "顾舟" in rendered
    assert "遗迹共鸣" in rendered
    for stale in ("债线纵横", "陆承", "贺苗", "人情债系统", "临江市"):
        assert stale not in rendered


def test_init_arc_preserves_contiguous_formal_chapters(tmp_path):
    from engine.agents.init_arc import build_state_from_paths

    output = tmp_path / "output"
    chapters = output / "chapters"
    chapters.mkdir(parents=True)
    setting_path = output / "setting_package.json"
    state_path = output / "orchestrator_state.json"
    setting_path.write_text(json.dumps(_setting(), ensure_ascii=False), encoding="utf-8")
    for chapter_no in (1, 2, 3):
        (chapters / f"ch_{chapter_no:04d}.txt").write_text(f"chapter {chapter_no}", encoding="utf-8")
    (chapters / "ch_0005.txt").write_text("gap", encoding="utf-8")

    state = build_state_from_paths(
        "project-x",
        setting_path=setting_path,
        state_path=state_path,
        chapters_dir=chapters,
    )

    assert state["current_chapter"] == 3
    assert state["chapter_task_queue"] == []
    assert state["arc_plans"][0]["arc_name"] == "归航"
    assert json.loads(state_path.read_text(encoding="utf-8"))["current_chapter"] == 3


def test_bootstrap_finalize_updates_project_scoped_state(tmp_path):
    from engine.tools.bootstrap import _finalize_selection

    output = tmp_path / "output"
    chapters = output / "chapters"
    chapters.mkdir(parents=True)
    (output / "setting_package.json").write_text(
        json.dumps(_setting(), ensure_ascii=False), encoding="utf-8"
    )
    for chapter_no in (1, 2, 3):
        (chapters / f"ch_{chapter_no:04d}_vA.txt").write_text(
            f"沈岚第{chapter_no}章正文。" * 30, encoding="utf-8"
        )
        (chapters / f"ch_{chapter_no:04d}_meta.json").write_text(
            json.dumps({"bootstrap": True}), encoding="utf-8"
        )
        assert _finalize_selection(
            chapter_no,
            "A",
            "project-x",
            manually_selected=False,
            novel_ai_dir=str(tmp_path),
        )

    assert (output / "style_samples" / "anchor_ch01.txt").is_file()
    state = json.loads((output / "orchestrator_state.json").read_text(encoding="utf-8"))
    assert state["current_chapter"] == 3
    memory = json.loads(
        (tmp_path / "memory" / "l2" / "project-x_memory.json").read_text(encoding="utf-8")
    )
    assert "沈岚" in memory["hot"]["recent_events"]
    for chapter_no in (1, 2, 3):
        meta = json.loads(
            (chapters / f"ch_{chapter_no:04d}_meta.json").read_text(encoding="utf-8")
        )
        assert meta["automatically_selected"] is True
        assert meta["manually_selected"] is False


def test_approved_db_outline_syncs_into_project_state(tmp_path):
    from app.api.bridge import _sync_approved_outlines
    from app.database import SessionLocal
    from app.models import Outline, Project

    db = SessionLocal()
    project_id = "approved-outline-sync"
    db.add(Project(id=project_id, title="同步测试", genre="科幻", config_json={}))
    db.add(Outline(
        project_id=project_id,
        arc_id=1,
        arc_name="归航",
        arc_goal="找到舰队",
        arc_estimated_chapters=1,
        status="approved",
        outline_json=[{"chapter_number": 99, "chapter_goal": "采用数据库大纲"}],
    ))
    db.commit()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    state_path = config_dir / "orchestrator_state.json"
    state_path.write_text(json.dumps({"approved_outline_tasks": {}}), encoding="utf-8")

    result = _sync_approved_outlines(project_id, str(tmp_path), db)

    assert result == {"approved_arcs": [1], "task_count": 1}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["approved_outline_tasks"]["1"][0]["chapter_goal"] == "采用数据库大纲"
    db.query(Outline).filter_by(project_id=project_id).delete()
    db.query(Project).filter_by(id=project_id).delete()
    db.commit()
    db.close()


def test_orchestrator_prefers_approved_outline_and_renumbers(tmp_path, monkeypatch):
    from engine import orchestrator as orch
    from engine.state import create_initial_state

    monkeypatch.setattr(orch, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(orch, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(orch, "get_l2", lambda _novel_id: {"hot": {}})
    state = create_initial_state("project-x", "标题", "personal", "科幻", "概念")
    state["current_chapter"] = 3
    state["arc_plans"] = [{"arc_id": 1, "arc_name": "归航", "estimated_chapters": 2}]
    state["total_arcs_planned"] = 1
    state["approved_outline_tasks"] = {
        "1": [
            {"chapter_number": 20, "chapter_goal": "任务一"},
            {"chapter_number": 21, "chapter_goal": "任务二"},
        ]
    }

    result = orch.node_load_arc_tasks(state)

    assert [task["chapter_number"] for task in result["chapter_task_queue"]] == [4, 5]
    assert result["chapter_task_queue"][0]["chapter_goal"] == "任务一"
    assert "任务二" in result["chapter_task_queue"][1]["chapter_goal"]
    assert result["chapter_task_queue"][1]["is_final_chapter"] is True
