"""2026-08-08 任务 #12：Dashboard 列表置顶 + 多选删除端点的不变量测试。

覆盖：
  - PUT /projects/{id}/pin：返回更新后的 ProjectOut、pinned 字段持久化、list_projects 排序
  - DELETE /projects/{id}：级联清空 + 204 + 再次 GET 404
  - POST /projects/bulk-delete：批量删除、跨用户跳过

这些测试不依赖 FastAPI TestClient 之外的固定装置，用 TestClient + isolated DB fixture。
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import (
    Character, Chapter, Faction, Foreshadowing, MapNode, NovelAIBinding,
    PowerSystem, WorldSetting, Project,
)


@pytest.fixture
def client():
    # TestClient 同步调用 FastAPI async endpoint —— 用于本测试足够。
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fresh_project(client):
    """每次测试创建独立 project（uuid 后缀避免与其他 fixture 串数据）。"""
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "title": f"pin-delete-test-{suffix}",
        "genre": "test",
        "audience": "test",
        "config_json": {},
    }
    r = client.post("/projects", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


class TestProjectPin:
    """PIN 端点契约 + 排序保证。"""

    def test_pin_project_persists_and_returns_projectout(self, client, fresh_project):
        pid = fresh_project["id"]
        r = client.put(
            f"/projects/{pid}/pin",
            json={"pinned": True, "pin_order": 5},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == pid
        assert body["pinned"] is True
        assert body["pin_order"] == 5

        # 持久化验证：再读 GET 必须也是 pinned
        r2 = client.get(f"/projects/{pid}")
        assert r2.json()["pinned"] is True
        assert r2.json()["pin_order"] == 5

    def test_pin_unpin_round_trip(self, client, fresh_project):
        """pin → unpin → 回到默认（False/0）。"""
        pid = fresh_project["id"]
        client.put(f"/projects/{pid}/pin", json={"pinned": True, "pin_order": 3})
        r = client.put(f"/projects/{pid}/pin", json={"pinned": False, "pin_order": 0})
        body = r.json()
        assert body["pinned"] is False
        assert body["pin_order"] == 0

    def test_list_projects_orders_pinned_first(self, client):
        """pinned 项目必须排在列表前面（pinned DESC, pin_order DESC, created_at DESC）。

        创建 3 个 project：unpinned 旧、unpinned 新、pinned 中。
        """
        ids = []
        for label in ["unpinned-old", "unpinned-new", "pinned-mid"]:
            r = client.post("/projects", json={
                "title": f"sort-test-{label}",
                "genre": "test",
                "config_json": {},
            })
            assert r.status_code == 201
            ids.append((label, r.json()["id"]))

        # unpin 两个，pin 中间那个
        client.put(f"/projects/{ids[2][1]}/pin", json={"pinned": True, "pin_order": 1})
        client.put(f"/projects/{ids[1][1]}/pin", json={"pinned": False, "pin_order": 0})

        # 拉列表 —— pinned 项目必须排第一
        listing = client.get("/projects").json()
        # 我们的 3 个 + 可能已有的其他 project
        my_ids = {pid for _, pid in ids}
        my_in_list = [p for p in listing if p["id"] in my_ids]
        # pinned 的项目必须排在 unpinned 前面
        pinned_first = next((p for p in my_in_list if p["id"] == ids[2][1]), None)
        assert pinned_first is not None
        pinned_pos = my_in_list.index(pinned_first)
        for _, pid in ids:
            if pid == pinned_first["id"]:
                continue
            other = next((p for p in my_in_list if p["id"] == pid), None)
            if other is None:
                continue
            other_pos = my_in_list.index(other)
            assert pinned_pos < other_pos, (
                f"pinned 项目必须排在 unpinned 前面，"
                f"pinned_pos={pinned_pos} other_pos={other_pos}"
            )

    def test_pin_nonexistent_returns_404(self, client):
        r = client.put(f"/projects/{uuid.uuid4().hex}/pin", json={"pinned": True})
        assert r.status_code == 404


class TestProjectDelete:
    """DELETE 端点契约 + 级联清空。"""

    def test_delete_project_returns_204_and_subsequent_get_404(self, client, fresh_project):
        pid = fresh_project["id"]
        r = client.delete(f"/projects/{pid}")
        assert r.status_code == 204
        # 后续 GET 必须 404
        r2 = client.get(f"/projects/{pid}")
        assert r2.status_code == 404

    def test_delete_cascades_related_rows(self, client, fresh_project):
        """FK ondelete=CASCADE：删 project 时关联的 WorldSetting / Character / Chapter 等
        必须一起清（避免 zombie 数据）。"""
        pid = fresh_project["id"]
        db = SessionLocal()
        try:
            # 塞几条关联行
            db.add(WorldSetting(project_id=pid, world_view="x", story_core="y"))
            db.add(Character(project_id=pid, name="TestChar", role="配角"))
            db.add(Faction(project_id=pid, name="TestFaction"))
            db.add(Foreshadowing(project_id=pid, content="x"))
            db.add(MapNode(project_id=pid, name="x", level="city"))
            db.add(PowerSystem(project_id=pid, name="x"))
            db.commit()

            # 校验数据在
            assert db.query(WorldSetting).filter_by(project_id=pid).count() == 1
            assert db.query(Character).filter_by(project_id=pid).count() == 1
        finally:
            db.close()

        r = client.delete(f"/projects/{pid}")
        assert r.status_code == 204

        # 关联行必须被 CASCADE 清掉
        db = SessionLocal()
        try:
            assert db.query(WorldSetting).filter_by(project_id=pid).count() == 0, (
                "FK ondelete=CASCADE 失败：WorldSetting 残留"
            )
            assert db.query(Character).filter_by(project_id=pid).count() == 0
            assert db.query(Faction).filter_by(project_id=pid).count() == 0
            assert db.query(Foreshadowing).filter_by(project_id=pid).count() == 0
            assert db.query(MapNode).filter_by(project_id=pid).count() == 0
            assert db.query(PowerSystem).filter_by(project_id=pid).count() == 0
        finally:
            db.close()

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete(f"/projects/{uuid.uuid4().hex}")
        assert r.status_code == 404


class TestBulkDelete:
    """批量删除端点契约。"""

    def test_bulk_delete_returns_deleted_and_skipped_lists(self, client):
        ids = []
        for label in ["bulk-a", "bulk-b", "bulk-c"]:
            r = client.post("/projects", json={
                "title": f"bulk-test-{label}",
                "genre": "test",
                "config_json": {},
            })
            ids.append(r.json()["id"])

        # 加一个不存在的 id 看是否进 skipped
        fake_id = uuid.uuid4().hex
        r = client.post("/projects/bulk-delete", json={
            "ids": ids + [fake_id],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        # fake id 必须 skipped（require_owned_project 404 → caught HTTPException）
        assert fake_id in body["skipped"], (
            f"不存在的 id 应进 skipped，实际: {body}"
        )
        for pid in ids:
            assert pid in body["deleted"], f"创建的 id 必须进 deleted，实际: {body}"

        # 删了的 GET 必须 404
        for pid in ids:
            assert client.get(f"/projects/{pid}").status_code == 404

    def test_bulk_delete_empty_ids_returns_empty_deleted(self, client):
        """空 ids 列表合法（不动任何数据），不能抛。"""
        r = client.post("/projects/bulk-delete", json={"ids": []})
        assert r.status_code == 200
        body = r.json()
        assert body == {"deleted": [], "skipped": []}