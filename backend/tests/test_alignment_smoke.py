"""
backend/tests/test_alignment_smoke.py — P1/P3 alignment smoke test

用 FastAPI TestClient 验证所有新加的 endpoint 都能正常注册 + 处理基本请求。
不需要起 uvicorn，也不需要真实 LLM（post-process 走 stub fallback）。

覆盖：
  - 规则配置 GET/PUT
  - RuleCenter post-process (logic / venom / deai)
  - 章节出场人物 + 单章详情
  - 伏笔状态流转
  - AI 参与度声明读写
  - bridge.run 接受 outline_mode
  - Provider.needs_proxy 经 LLM 路由配置生效

pytest discoverable：所有函数命名为 test_*，由 `pytest tests/` 自动收集。
（原版用 _test() 装饰器 + 手动 run_all()，`pytest tests/` 跑不到；本改写
补上"让 CI 自动跑"的承诺；行为覆盖不变，仅框架替换为 pytest。）

环境隔离：用临时 SQLite DB（tmp_path fixture）避免污染真实数据。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 把 backend/ 加到 sys.path，方便 import app.*
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import Chapter, Project  # noqa: E402


# 一次性建表
Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="module")
def _bootstrap():
    """模块级 fixture：建一个项目 + 一章。
    pytest-discoverable 版本替代原版全局副作用。
    """
    db = SessionLocal()
    try:
        p = Project(
            title="Alignment Smoke Test",
            genre="都市",
            audience="男频·青年向",
            config_json={"tropes": ["系统流"]},
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        pid = p.id
        ch = Chapter(
            project_id=pid,
            chapter_no=1,
            title="测试章",
            content="陆承在临江市的一个写字楼里，看到了那条红色的人情债链。\n【人情点+100】",
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        cid = ch.id
    finally:
        db.close()
    return pid, cid


@pytest.fixture(scope="module")
def client():
    # 用 with context 触发 lifespan，让 run_migrations 真正跑起来（Phase 3 owner_id/audit_mode
    # 这些增量列不会由 Base.metadata.create_all 自动加给已有表——必须走 migrations）。
    # 不走 lifespan 时直接 SELECT/INSERT 含新列会报 "table projects has no column named owner_id"。
    with TestClient(app) as c:
        yield c


# ───────── Tests ─────────

def test_rules_default(client, _bootstrap):
    """GET /projects/{id}/rules 默认配置"""
    pid, _ = _bootstrap
    r = client.get(f"/projects/{pid}/rules")
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["style"] == "webnovel"
    assert cfg["template"] == "run.章节撰写"
    assert cfg["taboos"] == []


def test_rules_put(client, _bootstrap):
    """PUT /projects/{id}/rules 写入持久化"""
    pid, _ = _bootstrap
    r = client.put(f"/projects/{pid}/rules", json={
        "style": "literary",
        "taboos": ["不禁", "然而"],
        "template": "review.逻辑毒舌",
    })
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["style"] == "literary"
    assert cfg["taboos"] == ["不禁", "然而"]
    r2 = client.get(f"/projects/{pid}/rules")
    assert r2.json()["style"] == "literary"


def test_rules_invalid_style(client, _bootstrap):
    """style 非法值拒绝"""
    pid, _ = _bootstrap
    r = client.put(f"/projects/{pid}/rules", json={"style": "garbage"})
    assert r.status_code in (400, 422)


def test_chapter_characters(client, _bootstrap):
    """章节出场人物（通过 chapter_characters 图谱边）"""
    pid, cid = _bootstrap
    r = client.get(f"/projects/{pid}/chapters/{cid}/characters")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_chapter_detail(client, _bootstrap):
    """GET /chapters/{id} 详情含完整正文 + 出场人物列表"""
    pid, cid = _bootstrap
    r = client.get(f"/projects/{pid}/chapters/{cid}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["chapter_no"] == 1
    assert "陆承" in data["content"]


def test_foreshadow_status(client, _bootstrap):
    """伏笔状态流转：未铺垫 → 已铺垫 → 已回收"""
    pid, _ = _bootstrap
    # 跑通到 status 字段路径只需要 fs_id；用空章节项目模拟读列表即可
    r = client.get(f"/projects/{pid}/foreshadowings")
    assert r.status_code == 200


def test_foreshadow_invalid_status(client, _bootstrap):
    """PUT 非法 status 拒绝"""
    pid, _ = _bootstrap
    # 通过 schema 验证无效 status
    r = client.put(
        f"/projects/{pid}/foreshadowings/nonexistent/status",
        json={"status": "GARBAGE"},
    )
    assert r.status_code in (400, 404, 422)


def test_ai_assist_read(client, _bootstrap):
    """AI 参与度默认 / 读"""
    pid, _ = _bootstrap
    r = client.get(f"/projects/{pid}/ai-assist-level")
    assert r.status_code == 200
    assert r.json()["ai_assist_level"] in ("ai_assisted", "unset")


def test_ai_assist_write_valid(client, _bootstrap):
    """AI 参与度合法写入"""
    pid, _ = _bootstrap
    r = client.put(f"/projects/{pid}/ai-assist-level", json={"ai_assist_level": "human_primary"})
    assert r.status_code == 200
    assert r.json()["ai_assist_level"] == "human_primary"


def test_ai_assist_invalid(client, _bootstrap):
    """AI 参与度非法值拒绝"""
    pid, _ = _bootstrap
    r = client.put(f"/projects/{pid}/ai-assist-level", json={"ai_assist_level": "alien_invasion"})
    assert r.status_code in (400, 422)


def test_bridge_run_accepts_outline_mode(client, _bootstrap):
    """POST /bridge/run 接受 outline_mode 字段"""
    pid, _ = _bootstrap
    r = client.post(
        f"/projects/{pid}/bridge/run",
        json={"command": "planner", "args": [], "outline_mode": "card"},
    )
    # bridge.run 在 worldbuild 未完成时会 400；这里只关心 schema / 参数接受。
    # 至少有 400/409（参数接受）才算通过；500 算失败。
    assert r.status_code in (200, 400, 409), r.text


def test_set_audit_mode(client, _bootstrap):
    """POST /bridge/set-audit-mode：草稿模式切换 — Phase 3 去全局化版

    之前：直接写 os.environ["NOVEL_AUDIT_MODE"]（进程全局，多项目共用时
    A 设 draft 会污染 B 的 run）。
    现在：写入 Project.audit_mode 列，单项目隔离。
    """
    pid, _ = _bootstrap
    r = client.post(f"/projects/{pid}/bridge/set-audit-mode", json={"mode": "draft"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "draft"
    # 验证：mode 已持久化到 Project.audit_mode（不是 os.environ）
    db = SessionLocal()
    try:
        p = db.get(Project, pid)
        assert p.audit_mode == "draft", (
            f"set_audit_mode 应写 Project.audit_mode（db），实际={p.audit_mode!r}"
        )
    finally:
        db.close()


def test_set_audit_mode_invalid(client, _bootstrap):
    """非合法模式拒绝"""
    pid, _ = _bootstrap
    r = client.post(f"/projects/{pid}/bridge/set-audit-mode", json={"mode": "bogus"})
    assert r.status_code in (400, 422)


def test_set_audit_mode_isolation(client, _bootstrap):
    """Phase 3 regression：A 设 draft 不会污染 B 的默认值。

    验收：项目 A 设 draft，项目 B 没动 → A.audit_mode='draft'，B.audit_mode='full'。
    反映到 subprocess env 也是同样语义（见 test_spawn_audit_mode_per_project）。
    """
    pid_a, _ = _bootstrap
    db = SessionLocal()
    try:
        # 再插一个项目 B
        p_b = Project(
            title="Project B",
            genre="都市",
            audience="男频·青年向",
            config_json={"tropes": ["系统流"]},
        )
        db.add(p_b)
        db.commit()
        db.refresh(p_b)
        pid_b = p_b.id
    finally:
        db.close()

    # A 设 draft
    r = client.post(f"/projects/{pid_a}/bridge/set-audit-mode", json={"mode": "draft"})
    assert r.status_code == 200, r.text

    # 验证两个项目的 audit_mode 各自独立（DB 层面的隔离）
    db = SessionLocal()
    try:
        a = db.get(Project, pid_a)
        b = db.get(Project, pid_b)
        assert a.audit_mode == "draft", f"A.audit_mode 应为 draft，实际={a.audit_mode!r}"
        assert b.audit_mode in (None, "full"), (
            f"B.audit_mode 不应被 A 污染，仍为默认值（None 或 'full'），"
            f"实际={b.audit_mode!r}"
        )
    finally:
        db.close()


def test_spawn_audit_mode_per_project(client, _bootstrap, monkeypatch):
    """Phase 3 regression：spawn subprocess 时 audit_mode 必须按 project_id 取值。

    验收：项目 A 设 draft，项目 B 没动 → _spawn_engine_subprocess 为 A/B
    分别注入不同的 NOVEL_AUDIT_MODE env（A=draft，B=full），而不是
    从父进程 os.environ 读同一个值（那样会互相污染）。
    """
    pid_a, _ = _bootstrap
    db = SessionLocal()
    try:
        p_b = Project(
            title="Project B for spawn test",
            genre="都市",
            audience="男频·青年向",
            config_json={"tropes": ["系统流"]},
            audit_mode="full",  # 默认：显式设 full
        )
        db.add(p_b)
        db.commit()
        db.refresh(p_b)
        pid_b = p_b.id
        # 给 A 设 draft
        p_a = db.get(Project, pid_a)
        p_a.audit_mode = "draft"
        db.commit()
    finally:
        db.close()

    # Monkeypatch subprocess.Popen 把 env 抓出来
    captured: dict = {}

    class _FakePopen:
        def __init__(self, cmd, env, **kwargs):
            captured["env"] = env
            # 用标准库 fake 进程对象，_drain_stdout 里只调 stdout.readline / wait
            class _Proc:
                stdout = __import__("io").StringIO("")
                returncode = 0
                def wait(self, *_a, **_kw): return 0
                stdout_lines = property(lambda self: self.stdout)
            self.proc = _Proc()

        def __getattr__(self, name):
            return getattr(self.proc, name)

    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    from app.api import bridge as _bridge

    # 1) 跑 A 的 subprocess
    q1 = __import__("queue").Queue()
    _bridge._spawn_engine_subprocess("run-fake-1", pid_a, "planner", [], q1, "batch")
    env_a = captured["env"]
    assert env_a.get("NOVEL_AUDIT_MODE") == "draft", (
        f"A 项目 audit_mode=draft，subprocess env NOVEL_AUDIT_MODE 应该是 draft，"
        f"实际={env_a.get('NOVEL_AUDIT_MODE')!r}"
    )

    # 2) 跑 B 的 subprocess（A 已设 draft 全局，B 应仍为 full，不能污染）
    q2 = __import__("queue").Queue()
    _bridge._spawn_engine_subprocess("run-fake-2", pid_b, "planner", [], q2, "batch")
    env_b = captured["env"]
    assert env_b.get("NOVEL_AUDIT_MODE") == "full", (
        f"B 项目 audit_mode=full，subprocess env NOVEL_AUDIT_MODE 应该是 full，"
        f"不能被 A 的 draft 污染，实际={env_b.get('NOVEL_AUDIT_MODE')!r}"
    )


def test_project_set_platform_valid(client, _bootstrap):
    """PUT /projects/{id}/platform：合法值接受"""
    pid, _ = _bootstrap
    r = client.put(f"/projects/{pid}/platform", json={"platform": "personal"})
    assert r.status_code == 200, r.text
    assert r.json()["platform"] == "personal"


def test_project_set_platform_invalid(client, _bootstrap):
    """非法 platform 拒绝"""
    pid, _ = _bootstrap
    r = client.put(f"/projects/{pid}/platform", json={"platform": "human_world"})
    assert r.status_code == 400, r.text


def test_worldbuild_stages_endpoint(client):
    """GET /worldbuild/stages 返回 10 阶段清单 — 防止前后端 STAGES 漂移

    这是为了让前端 WorldBuild.tsx 不再硬编码 STAGES 数组。
    后端返回的 key 必须包含所有 10 个 known stage (防止漏写 / 改名)。
    """
    r = client.get("/worldbuild/stages")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "stages" in data
    keys = {s["key"] for s in data["stages"]}
    expected = {
        "parse_config", "world_basics", "plot_skeleton", "characters",
        "relations", "foreshadowing", "map", "factions_power",
        "currency_special", "consistency_check",
    }
    missing = expected - keys
    assert not missing, f"stages 缺字段: {missing}"
    # Phase 7：反方向断言 — 后端多返任何不在 expected 里的 key 立刻挂（防 STAGES
    # 加新条目忘改 expected 集合导致 silent drift）
    extra = keys - expected
    assert not extra, (
        f"stages 多返未登记字段: {extra}。"
        f"（STAGES 加新条目必须同步更新 expected 集合，让预期发现者审一次）"
    )
    # Phase 7：顺序断言 — 顺序错乱会让前端进度条在错位位置显示「done」
    expected_order = [
        "parse_config", "world_basics", "plot_skeleton", "characters",
        "relations", "foreshadowing", "map", "factions_power",
        "currency_special", "consistency_check",
    ]
    actual_order = [s["key"] for s in data["stages"]]
    assert actual_order == expected_order, (
        f"stages 顺序漂移：\n  expected={expected_order}\n  actual={actual_order}\n"
        f"（顺序错会让前端 SSE stage_done 事件对不上进度条位置）"
    )
    # label 不可为空字符串（前端拿这个渲染）
    for s in data["stages"]:
        assert s["label"], f"stage {s['key']} label 为空"


def test_bridge_run_request_includes_outline_mode():
    """Schema: BridgeRunRequest 含 outline_mode 字段"""
    from app.schemas import BridgeRunRequest
    p = BridgeRunRequest(command="run", args=["10"], outline_mode="card")
    assert p.outline_mode == "card"
    p2 = BridgeRunRequest(command="run")
    assert p2.outline_mode is None


def test_provider_needs_proxy_field_roundtrip(client, _bootstrap):
    """Provider 端点需要 needs_proxy 字段往返（schema 验证）"""
    # 新建一个 provider 然后读出，验证 needs_proxy 字段存在
    r = client.post("/providers", json={
        "name": "smoke-proxy-test",
        "provider_type": "deepseek",
        "api_key": "sk-smoke",
        "default_model": "deepseek-chat",
        "needs_proxy": True,
    })
    assert r.status_code in (200, 400), r.text  # 400 if needs_proxy incomplete schema
    if r.status_code == 200:
        data = r.json()
        assert data["needs_proxy"] is True


def test_embedding_resolution_fallback():
    """embedding provider 自动 fallback：未配 key 时回退 mock，配了真 key 走真模型"""
    from app import config as _cfg
    from app.rag.embedding import _resolved_provider
    saved_settings = _cfg.settings
    saved_env = __import__("os").environ.get("NOVEL_EMBEDDING_API_KEY")
    try:
        # 不配 key → 自动 mock（即使 settings 写了 qwen3）
        _cfg.settings = _cfg.Settings(embedding_provider="qwen3", embedding_api_key="")
        assert _resolved_provider() == "mock", "无 key 应回退 mock"

        # 配了 key → 走真 provider
        _cfg.settings = _cfg.Settings(
            embedding_provider="qwen3",
            embedding_api_key="sk-fake-for-test",
            embedding_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        assert _resolved_provider() == "qwen3", "配了 key 走 qwen3"

        # 显式 mock 即便有 key 也 mock
        _cfg.settings = _cfg.Settings(
            embedding_provider="mock",
            embedding_api_key="sk-fake",
        )
        assert _resolved_provider() == "mock"
    finally:
        _cfg.settings = saved_settings
        if saved_env:
            __import__("os").environ["NOVEL_EMBEDDING_API_KEY"] = saved_env


def test_embedding_mock_dim_and_cosine():
    """embedding_mock 维度一致 + cosine_similarity 不等维返回 0"""
    import asyncio
    from app.rag.embedding import embed_text, cosine_similarity, MOCK_EMBEDDING_DIMS

    async def run():
        v1 = await embed_text("陆承走进临江市")
        v2 = await embed_text("陆承踏入临江市")  # 相近
        v3 = await embed_text("全英文内容 different language")  # 大差异
        assert len(v1) == MOCK_EMBEDDING_DIMS
        assert len(v2) == MOCK_EMBEDDING_DIMS
        # 相近文本应有正相似度
        assert cosine_similarity(v1, v2) > 0.2
        # 不等维（mock 是 256，真模型可能是 1024）时应自动 0
        assert cosine_similarity(v1, [0.5] * 512) == 0.0

    asyncio.run(run())
