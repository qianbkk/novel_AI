"""test_pre_production_api_2026_08_17.py

v1.0 Stage B API 验证：theme_spine + genre_profile CRUD endpoints 必须：
- GET 不存在 → 404
- PUT 缺字段 → 400（不让损坏数据落盘）
- PUT 成功后 GET 返回相同数据
- POST /generate 调用设计函数（mock LLM）
- owner_id 校验（multi-user 安全）

使用 test_auth.py 同款 module-level DATABASE_URL + autouse _clean_state 模式，
不依赖 conftest.api_client（避免 fixture 链路对 isolated_test_db 隐式依赖）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid as _uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# 独立临时 DB（避免污染其他测试）
_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_db.close()
_tmp_db_path = _tmp_db.name + f".{_uuid.uuid4().hex[:6]}.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db_path}"
os.environ["JWT_SECRET"] = "test-secret-for-pytest-only-this-is-a-long-enough-key-1234567890"

from app.config import Settings as _Settings  # noqa: E402
import app.config as _cfg  # noqa: E402
_cfg.settings = _Settings()  # noqa: E402  reset cache

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Project  # noqa: E402

import pytest  # noqa: E402

# 一次性建表
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_state():
    """每个测试前清表（drop + create）+ 清 NOVEL_AI_DIR。"""
    from app.database import Base as _B, engine as _e
    _B.metadata.drop_all(bind=_e)
    _B.metadata.create_all(bind=_e)
    os.environ.pop("NOVEL_AI_DIR", None)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def project_with_novel_ai_dir(client, tmp_path, monkeypatch):
    """建一个测试 project + 把 novel_ai_dir 重定向到 tmp_path。"""
    pid = "test-pre-prod"
    db = SessionLocal()
    try:
        proj = db.get(Project, pid)
        if proj is None:
            proj = Project(id=pid, title="测试", genre="历史", config_json={})
            db.add(proj)
            db.commit()
    finally:
        db.close()

    (tmp_path / "output").mkdir(exist_ok=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))

    yield {"project_id": pid, "novel_ai_dir": tmp_path, "client": client}

    db = SessionLocal()
    try:
        db.query(Project).filter_by(id=pid).delete()
        db.commit()
    finally:
        db.close()


# ── 1. GET 不存在 → 404 ─────────────────────────

def test_get_genre_profile_not_found(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    r = client.get(f"/projects/{pid}/pre-production/genre-profile")
    assert r.status_code == 404


def test_get_theme_not_found(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    r = client.get(f"/projects/{pid}/pre-production/theme")
    assert r.status_code == 404


# ── 2. POST /generate genre-profile 走模板 ─────────────────────────

def test_generate_genre_profile_without_llm(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    r = client.post(
        f"/projects/{pid}/pre-production/genre-profile/generate",
        json={"genre_key": "lishi", "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["genre_key"] == "lishi"
    assert data["research_strength"] == "strong"
    profile_path = project_with_novel_ai_dir["novel_ai_dir"] / "output" / "genre_profile.json"
    assert profile_path.is_file()
    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved["genre_key"] == "lishi"


def test_generate_genre_profile_unknown_genre_rejected(client, project_with_novel_ai_dir):
    """未知 genre → 400（不让 silently 落盘 placeholder）。"""
    pid = project_with_novel_ai_dir["project_id"]
    r = client.post(
        f"/projects/{pid}/pre-production/genre-profile/generate",
        json={"genre_key": "unknown_xyz", "use_llm": False},
    )
    assert r.status_code == 400
    assert "未知" in r.json()["detail"]


# ── 3. PUT theme 缺字段 → 400 ─────────────────────────

def test_put_theme_rejects_missing_field(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    r = client.put(
        f"/projects/{pid}/pre-production/theme",
        json={"theme_statement": "缺其它字段", "source": "user"},
    )
    # 422 = Pydantic 校验失败（缺 expectation_arc / resonance_anchors），
    # 400 = save_theme 内部校验失败。两者都是合法拒绝。
    assert r.status_code in (400, 422)


def test_put_theme_rejects_empty_statement(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    r = client.put(
        f"/projects/{pid}/pre-production/theme",
        json={
            "theme_statement": "",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["a", "b", "c"],
            "source": "user",
        },
    )
    assert r.status_code == 400


# ── 4. PUT theme 完整 → 200，再 GET 拿到相同数据 ─────────────────────────

def test_put_theme_full_then_get(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    theme = {
        "theme_statement": "我手工改的主题",
        "expectation_arc": {
            "seed_chapter": 1, "payoff_chapter": 80, "twist_chapter": 25,
            "description": "用户编辑的弧",
        },
        "resonance_anchors": ["家", "忠诚", "孤独"],
        "source": "user",
    }
    r = client.put(f"/projects/{pid}/pre-production/theme", json=theme)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "saved"

    r2 = client.get(f"/projects/{pid}/pre-production/theme")
    assert r2.status_code == 200
    assert r2.json()["theme_statement"] == "我手工改的主题"
    assert r2.json()["source"] == "user"


def test_put_theme_forces_source_user(client, project_with_novel_ai_dir):
    """即使客户端送 source='llm'，PUT 必须强制改写为 user（PUT = UI 编辑路径）。"""
    pid = project_with_novel_ai_dir["project_id"]
    theme = {
        "theme_statement": "测试 source 字段",
        "expectation_arc": {
            "seed_chapter": 1, "payoff_chapter": 50, "twist_chapter": 20,
            "description": "x",
        },
        "resonance_anchors": ["a", "b", "c"],
        "source": "llm",  # 客户端想伪造
    }
    r = client.put(f"/projects/{pid}/pre-production/theme", json=theme)
    assert r.status_code == 200
    r2 = client.get(f"/projects/{pid}/pre-production/theme")
    assert r2.json()["source"] == "user", "PUT 必须强制 source='user'"


# ── 5. POST /theme/generate 调设计函数（mock LLM） ─────────────────────────

def test_generate_theme_with_llm(client, project_with_novel_ai_dir, monkeypatch):
    """POST /theme/generate 调 design_theme(use_llm=True)，LLM 走 mock。"""
    pid = project_with_novel_ai_dir["project_id"]
    from engine.agents import theme_designer as td_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            return (
                '{"theme_statement": "LLM 改写", "expectation_arc": '
                '{"seed_chapter": 1, "payoff_chapter": 100, "twist_chapter": 30, '
                '"description": "LLM 弧"}, '
                '"resonance_anchors": ["LLM锚1", "LLM锚2", "LLM锚3"]}',
                0.01,
            )

    monkeypatch.setattr(td_mod, "get_active_router", lambda: _FakeRouter())

    client.post(
        f"/projects/{pid}/pre-production/genre-profile/generate",
        json={"genre_key": "lishi", "use_llm": False},
    )

    r = client.post(
        f"/projects/{pid}/pre-production/theme/generate",
        json={"concept": "归家", "use_llm": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["theme_statement"] == "LLM 改写"
    assert data["source"] == "llm"
    assert len(data["resonance_anchors"]) >= 3


def test_generate_theme_without_llm_uses_template(client, project_with_novel_ai_dir):
    """use_llm=False → 模板生成（CI 友好，不调真 LLM）。"""
    pid = project_with_novel_ai_dir["project_id"]
    client.post(
        f"/projects/{pid}/pre-production/genre-profile/generate",
        json={"genre_key": "lishi", "use_llm": False},
    )
    r = client.post(
        f"/projects/{pid}/pre-production/theme/generate",
        json={"concept": "", "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["theme_statement"]
    assert data["source"] == "template"


# ── 6. 项目不存在 → 404 ─────────────────────────

def test_get_theme_project_not_found(client):
    r = client.get("/projects/nonexistent-xyz/pre-production/theme")
    assert r.status_code == 404


def test_put_theme_project_not_found(client):
    r = client.put(
        "/projects/nonexistent-xyz/pre-production/theme",
        json={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["a", "b", "c"],
        },
    )
    assert r.status_code == 404


# ── 7. Opening endpoints (Stage C: 黄金三章) ─────────────────────────

def _full_opening_payload() -> dict:
    """构造一个合法 opening_design dict（3 章全字段）。"""
    return {
        "chapter_1_anchor": {
            "scene": {"where": "工地外", "who_present": ["主角", "邻役"]},
            "hook_type": "悬念钩",
            "reader_emotion_to_install": "期待",
            "show_item_seed": "那双布鞋",
            "expectation_seed": "主角要回家了",
        },
        "chapter_2_question": {
            "scene": {"where": "驿站", "who_present": ["主角", "邻家少年"]},
            "hook_type": "对抗钩",
            "reader_question": "主角能不能带邻家少年一起逃？",
            "show_item_used": "布鞋被让给邻家少年",
            "expectation_shift": "归途多一个人",
        },
        "chapter_3_escalation": {
            "scene": {"where": "征兵处", "who_present": ["主角", "征兵官"]},
            "hook_type": "反转钩",
            "reader_emotion_to_install": "矛盾",
            "show_item_used": "布鞋被踏了一脚",
            "expectation_shift": "家方向变谜团",
        },
        "source": "user",
    }


def test_get_opening_not_found(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    r = client.get(f"/projects/{pid}/pre-production/opening")
    assert r.status_code == 404


def test_put_opening_full_then_get(client, project_with_novel_ai_dir):
    pid = project_with_novel_ai_dir["project_id"]
    payload = _full_opening_payload()

    r = client.put(f"/projects/{pid}/pre-production/opening", json=payload)
    assert r.status_code == 200, r.text

    r2 = client.get(f"/projects/{pid}/pre-production/opening")
    assert r2.status_code == 200
    assert r2.json()["source"] == "user"
    assert r2.json()["chapter_1_anchor"]["show_item_seed"] == "那双布鞋"


def test_put_opening_rejects_invalid_hook_type(client, project_with_novel_ai_dir):
    """非法 hook_type → 400（不能让下游渲染乱套）。"""
    pid = project_with_novel_ai_dir["project_id"]
    payload = _full_opening_payload()
    payload["chapter_1_anchor"]["hook_type"] = "但是法则"  # 不在 7 个合法 hook 内

    r = client.put(f"/projects/{pid}/pre-production/opening", json=payload)
    assert r.status_code == 400
    assert "hook_type" in r.json()["detail"] or "hook" in r.json()["detail"].lower()


def test_generate_opening_without_llm_uses_template(client, project_with_novel_ai_dir):
    """use_llm=False → 模板生成（CI 友好）。"""
    pid = project_with_novel_ai_dir["project_id"]
    # 先 gen genre profile + theme（opening_designer 用）
    client.post(
        f"/projects/{pid}/pre-production/genre-profile/generate",
        json={"genre_key": "lishi", "use_llm": False},
    )
    client.post(
        f"/projects/{pid}/pre-production/theme/generate",
        json={"concept": "", "use_llm": False},
    )

    r = client.post(
        f"/projects/{pid}/pre-production/opening/generate",
        json={"concept": "", "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    # 3 章都有
    for ch in ("chapter_1_anchor", "chapter_2_question", "chapter_3_escalation"):
        assert ch in data
        assert "hook_type" in data[ch]
    assert data["source"] == "template"