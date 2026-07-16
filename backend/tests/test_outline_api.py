"""Outline API 测试 — Issue #4 修复验证。

新增 endpoint（backend/app/api/outline.py）：
  GET    /projects/:id/outlines             — 列出所有弧
  GET    /projects/:id/outlines/:oid        — 单个弧详情
  POST   /projects/:id/outlines             — 手动创建（不调 LLM）
  PATCH  /projects/:id/outlines/:oid        — 编辑 / 改状态
  DELETE /projects/:id/outlines/:oid        — 删除
  POST   /projects/:id/outlines/generate    — 调 run_outline() 拿真实 chapter_goal

测试覆盖：
- CRUD 流程（创建 / 读 / 更新 / 删除）
- 状态流转（draft → approved → in_progress）
- 重复 arc_id 第二次 create 时（generate 模式）覆盖同 arc 记录
- 不存在的 outline_id 返回 404
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Project, Outline
import uuid


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def test_project(db):
    """创建测试项目（fixture 完成后清理）。"""
    pid = "test-outline-" + uuid.uuid4().hex[:8]
    p = Project(
        id=pid,
        title="测试大纲项目",
        genre="玄幻",
        audience="男频·青年向",
        config_json={},
    )
    db.add(p)
    db.commit()
    yield pid
    # 清理
    db.query(Outline).filter_by(project_id=pid).delete()
    db.query(Project).filter_by(id=pid).delete()
    db.commit()


# ──────────────────── CRUD ────────────────────


class TestOutlineCRUD:
    def test_list_empty(self, client, test_project):
        r = client.get(f"/projects/{test_project}/outlines")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_manual(self, client, test_project):
        payload = {
            "arc_id": 1,
            "arc_name": "觉醒",
            "arc_goal": "主角觉醒获得传承",
            "arc_estimated_chapters": 30,
        }
        r = client.post(f"/projects/{test_project}/outlines", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["arc_name"] == "觉醒"
        assert data["arc_goal"] == "主角觉醒获得传承"
        assert data["status"] == "draft"
        assert data["outline_json"] is None
        assert "id" in data

    def test_get_outline(self, client, test_project):
        c = client.post(f"/projects/{test_project}/outlines", json={
            "arc_id": 1, "arc_name": "修真", "arc_goal": "起点"
        }).json()
        r = client.get(f"/projects/{test_project}/outlines/{c['id']}")
        assert r.status_code == 200
        assert r.json()["arc_name"] == "修真"

    def test_get_not_found(self, client, test_project):
        r = client.get(f"/projects/{test_project}/outlines/nonexistent")
        assert r.status_code == 404

    def test_update_status(self, client, test_project):
        c = client.post(f"/projects/{test_project}/outlines", json={
            "arc_id": 1, "arc_name": "test"
        }).json()
        r = client.patch(f"/projects/{test_project}/outlines/{c['id']}", json={
            "status": "approved"
        })
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_update_outline_json(self, client, test_project):
        c = client.post(f"/projects/{test_project}/outlines", json={
            "arc_id": 1, "arc_name": "test"
        }).json()
        tasks = [
            {"chapter_number": 1, "chapter_role": "铺垫",
             "chapter_goal": "主角登场", "main_characters": ["主角"],
             "shuang_type": None, "shuang_description": "",
             "ending_hook_type": "悬念钩", "ending_hook_description": "",
             "setting_constraints": [], "forbidden_actions": [],
             "target_length": "2000-2200", "audit_mode": "full",
             "is_arc_climax": False}
        ]
        r = client.patch(f"/projects/{test_project}/outlines/{c['id']}", json={
            "outline_json": tasks
        })
        assert r.status_code == 200
        assert r.json()["outline_json"] == tasks

    def test_delete(self, client, test_project):
        c = client.post(f"/projects/{test_project}/outlines", json={
            "arc_id": 1, "arc_name": "to delete"
        }).json()
        r = client.delete(f"/projects/{test_project}/outlines/{c['id']}")
        assert r.status_code == 200
        # 验证已删除
        r2 = client.get(f"/projects/{test_project}/outlines/{c['id']}")
        assert r2.status_code == 404

    def test_list_multiple_sorted(self, client, test_project):
        for arc_id in [3, 1, 2]:
            client.post(f"/projects/{test_project}/outlines", json={
                "arc_id": arc_id, "arc_name": f"arc-{arc_id}"
            })
        r = client.get(f"/projects/{test_project}/outlines")
        arc_ids = [o["arc_id"] for o in r.json()]
        # 按 arc_id 升序
        assert arc_ids == [1, 2, 3]


# ──────────────────── generate 端点 ────────────────────


class TestOutlineGenerate:
    def test_generate_mock_llm(self, client, test_project, monkeypatch):
        """Mock run_outline 让测试不依赖真实 LLM。"""

        def fake_run_outline(arc, start, setting, memory):
            # 真实 run_outline 是 sync def，返回 (list[dict], float)
            return [
                {"chapter_number": 1, "chapter_role": "铺垫",
                 "chapter_goal": "主角登场", "main_characters": ["主角"],
                 "shuang_type": None, "shuang_description": "",
                 "ending_hook_type": "悬念钩", "ending_hook_description": "",
                 "setting_constraints": [], "forbidden_actions": [],
                 "target_length": "2000-2200", "audit_mode": "full",
                 "is_arc_climax": False},
                {"chapter_number": 2, "chapter_role": "发展",
                 "chapter_goal": "开始觉醒", "main_characters": ["主角"],
                 "shuang_type": "升级", "shuang_description": "获得传承",
                 "ending_hook_type": "信息钩", "ending_hook_description": "",
                 "setting_constraints": [], "forbidden_actions": [],
                 "target_length": "2200-2500", "audit_mode": "full",
                 "is_arc_climax": True},
            ], 0.0123

        # run_outline 是在 outline.py 里 import 的，需要 patch engine.agents.outline.run_outline
        from engine.agents import outline as outline_module
        monkeypatch.setattr(outline_module, "run_outline", fake_run_outline)

        payload = {
            "arc_id": 1,
            "arc_name": "觉醒",
            "arc_goal": "主角觉醒获得传承",
            "arc_estimated_chapters": 30,
        }
        r = client.post(f"/projects/{test_project}/outlines/generate", json=payload)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["arc_name"] == "觉醒"
        assert len(data["outline_json"]) == 2
        assert data["outline_json"][1]["is_arc_climax"] is True

    def test_generate_overwrites_same_arc_id(self, client, test_project, monkeypatch):
        """同 arc_id 第二次 generate 应该覆盖而不是新增。"""

        def fake_run_outline(arc, start, setting, memory):
            return [{
                "chapter_number": 1, "chapter_role": "发展",
                "chapter_goal": "new goal", "main_characters": [],
                "shuang_type": None, "shuang_description": "",
                "ending_hook_type": "信息钩", "ending_hook_description": "",
                "setting_constraints": [], "forbidden_actions": [],
                "target_length": "2000-2200", "audit_mode": "full",
                "is_arc_climax": False,
            }], 0.005

        from engine.agents import outline as outline_module
        monkeypatch.setattr(outline_module, "run_outline", fake_run_outline)

        # 第一次 generate
        client.post(f"/projects/{test_project}/outlines/generate", json={
            "arc_id": 1, "arc_name": "first", "arc_goal": "g1"
        })
        # 第二次 generate 同 arc_id
        client.post(f"/projects/{test_project}/outlines/generate", json={
            "arc_id": 1, "arc_name": "second", "arc_goal": "g2"
        })

        # 应该只有 1 条记录
        list_r = client.get(f"/projects/{test_project}/outlines")
        outlines = list_r.json()
        assert len(outlines) == 1
        assert outlines[0]["arc_name"] == "second"
        assert outlines[0]["outline_json"][0]["chapter_goal"] == "new goal"

    def test_generate_project_not_found(self, client):
        r = client.post("/projects/nonexistent/outlines/generate", json={
            "arc_id": 1, "arc_name": "x", "arc_goal": "y"
        })
        assert r.status_code == 404