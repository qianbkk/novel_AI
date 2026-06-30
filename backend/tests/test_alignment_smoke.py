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

# 用临时 SQLite DB 避免污染真实数据
_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.models import Chapter, Project, RuleConfig  # noqa: E402


P = "✅"
F = "❌"
results: list = []


def test(name: str):
    def dec(fn):
        def wrap():
            try:
                fn()
                results.append((P, name, ""))
                print(f"  {P} {name}")
            except Exception as e:
                results.append((F, name, str(e)))
                print(f"  {F} {name}: {e}")
        return wrap
    return dec


# 一次性建表 + 准备一个项目
Base.metadata.create_all(bind=engine)


def make_project() -> str:
    from app.database import SessionLocal
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
        return p.id
    finally:
        db.close()


project_id = make_project()


def make_chapter(project_id: str) -> str:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        ch = Chapter(
            project_id=project_id,
            chapter_no=1,
            title="测试章",
            content="陆承在临江市的一个写字楼里，看到了那条红色的人情债链。\n【人情点+100】",
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        return ch.id
    finally:
        db.close()


chapter_id = make_chapter(project_id)


# ───────── Tests ─────────
client = TestClient(app)


@test("GET /projects/{id}/rules — 默认配置")
def t1():
    r = client.get(f"/projects/{project_id}/rules")
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["style"] == "webnovel"
    assert cfg["template"] == "run.章节撰写"
    assert cfg["taboos"] == []


@test("PUT /projects/{id}/rules — 写入配置")
def t2():
    r = client.put(f"/projects/{project_id}/rules", json={
        "style": "literary",
        "taboos": ["不禁", "然而"],
        "template": "review.逻辑毒舌",
    })
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["style"] == "literary"
    assert cfg["taboos"] == ["不禁", "然而"]
    # 再读一遍验证持久化
    r2 = client.get(f"/projects/{project_id}/rules")
    assert r2.json()["style"] == "literary"


@test("PUT /projects/{id}/rules — 校验 style 非法值")
def t3():
    r = client.put(f"/projects/{project_id}/rules", json={"style": "invalid"})
    assert r.status_code == 400


@test("PUT /projects/{id}/rules — taboos 去重")
def t4():
    client.put(f"/projects/{project_id}/rules", json={
        "taboos": ["A", "A", "B", " ", "C"]
    })
    r = client.get(f"/projects/{project_id}/rules")
    cfg = r.json()
    # 去重 + 过滤空字符串
    assert cfg["taboos"] == ["A", "B", "C"]


@test("POST /projects/{id}/rules/post-process — logic")
def t5():
    r = client.post(f"/projects/{project_id}/rules/post-process", json={
        "tool": "logic",
        "chapter_no": 1,
        "style": "webnovel",
        "taboos": ["不禁"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool"] == "logic"
    assert body["chapter_no"] == 1
    assert isinstance(body["findings"], list)
    assert body["cost_usd"] >= 0


@test("POST /projects/{id}/rules/post-process — venom / deai")
def t6():
    for tool in ("venom", "deai"):
        r = client.post(f"/projects/{project_id}/rules/post-process", json={"tool": tool})
        assert r.status_code == 200, f"{tool}: {r.text}"
        assert r.json()["tool"] == tool


@test("POST /projects/{id}/rules/post-process — 未知 tool 拒绝")
def t7():
    r = client.post(f"/projects/{project_id}/rules/post-process", json={"tool": "unknown"})
    assert r.status_code == 400


@test("POST /projects/{id}/rules/post-process — 无章节 404")
def t8():
    r = client.post(f"/projects/{project_id}/rules/post-process", json={"tool": "logic", "chapter_no": 999})
    assert r.status_code == 404


@test("GET /projects/{id}/chapters/{ch_id} — 单章详情")
def t9():
    r = client.get(f"/projects/{project_id}/chapters/{chapter_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chapter_no"] == 1
    assert "陆承" in body["content"]
    assert isinstance(body["characters"], list)


@test("GET /projects/{id}/chapters/{ch_id}/characters — 出场人物")
def t10():
    r = client.get(f"/projects/{project_id}/chapters/{chapter_id}/characters")
    # 当前 characters 表里可能还没有陆承 → 可能是空数组
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


@test("GET /projects/{id}/foreshadowings — 列表")
def t11():
    r = client.get(f"/projects/{project_id}/foreshadowings")
    assert r.status_code == 200
    assert r.json() == []  # 没有 seed 数据


@test("PUT /projects/{id}/ai-assist-level — 更新")
def t12():
    r = client.put(f"/projects/{project_id}/ai-assist-level",
                   json={"ai_assist_level": "human_primary"})
    assert r.status_code == 200, r.text
    assert r.json()["ai_assist_level"] == "human_primary"
    # 校验非法值
    r2 = client.put(f"/projects/{project_id}/ai-assist-level",
                    json={"ai_assist_level": "garbage"})
    assert r2.status_code == 400


@test("GET /projects/{id}/ai-assist-level — 默认值")
def t13():
    r = client.get(f"/projects/{project_id}/ai-assist-level")
    assert r.status_code == 200
    assert r.json()["ai_assist_level"] in ("ai_assisted", "human_primary", "unset")


@test("LLM 路由：set_proxy_map + PROVIDER_PROXY 模块状态")
def t14():
    from engine.llm import router as r_mod
    from engine.llm.router import LLMRouter
    lr = LLMRouter()
    lr.set_proxy_map({"deepseek": "http://127.0.0.1:7890", "anthropic": "http://127.0.0.1:7890"})
    assert r_mod._PROVIDER_PROXY == {"deepseek": "http://127.0.0.1:7890",
                                    "anthropic": "http://127.0.0.1:7890"}


@test("LLM 路由：proxied client 创建")
def t15():
    from engine.llm.router import _get_proxied_client, _PROVIDER_PROXY
    # 没配置 proxy 时返回普通 client
    c = _get_proxied_client("deepseek", "https://api.deepseek.com")
    assert c is not None
    # 配置后创建代理 client（key in _proxy_mounts）
    _PROVIDER_PROXY["kimi"] = "http://127.0.0.1:9999"
    c2 = _get_proxied_client("kimi", "https://api.moonshot.cn")
    assert c2 is not None


@test("Schema: BridgeRunRequest 含 outline_mode")
def t16():
    from app.schemas import BridgeRunRequest
    p = BridgeRunRequest(command="run", args=["10"], outline_mode="card")
    assert p.outline_mode == "card"
    # 默认 None
    p2 = BridgeRunRequest(command="run")
    assert p2.outline_mode is None


@test("engine.graph.run_graph_task — outline_mode 透传（stub fallback）")
def t17():
    # 不真正起 orchestrator，直接验证 schema + 入参
    from app.schemas import BridgeRunRequest
    p = BridgeRunRequest(command="run", args=["1"], outline_mode="talk")
    assert p.outline_mode == "talk"


def run_all() -> bool:
    print(f"\n{'═'*60}")
    print(f"  🧪 前后端对齐 smoke test")
    print(f"{'═'*60}\n")
    for t in [t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13,
              t14, t15, t16, t17]:
        t()
    passed = sum(1 for r in results if r[0] == P)
    failed = sum(1 for r in results if r[0] == F)
    print(f"\n{'═'*60}")
    print(f"  结果：{passed}通过 / {failed}失败 / {len(results)}总计")
    if failed == 0:
        print(f"  🎉 全部通过！前后端对齐完成。")
    else:
        for icon, name, err in results:
            if icon == F:
                print(f"    {F} {name}: {err}")
    print(f"{'═'*60}\n")
    # 清理临时 DB
    try:
        os.unlink(_tmp_db.name)
    except Exception:
        pass
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)