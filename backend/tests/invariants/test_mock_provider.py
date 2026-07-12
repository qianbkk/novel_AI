"""mock_provider/ — Phase 3 测试拆分

不变量测试按业务域分文件存放。
原文件位置：tests/test_invariants.py（已替换为 re-export shim）
"""

from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(REPO_ROOT)
sys.path.insert(0, str(BACKEND))

# ── 原 test_invariants.py 顶部声明的 app.schema_validator 系列 ──
from app.schema_validator import (  # noqa: E402,F401
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,
)

class TestMockLLMProvider:
    """历史背景（独立审查标记的中危点）：
      之前要验证 engine 端到端机制（schema 校验、字数 budget、orchestrator
      编排、tools 调用）必须真花钱调 LLM。
      Mock provider 让这一切离线跑：单元测试 / 集成测试 / CI 都不依赖
      外部 API，引擎质量验证独立于生成质量。

      本轮新增：LLMRouter._mock 方法 + _MOCK_RESPONSES 模板。
      Mock 模式只验证引擎机制，不验证生成内容质量（生产仍走真 provider）。
    """

    def test_mock_provider_registered_in_dispatch(self):
        """LLMRouter 的 dispatch 必须包含 'mock' provider。"""
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        # 通过 routes 里把 agent 指向 mock，触发 dispatch
        r.routes["writer"] = ("mock", "mock-model")
        text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text, "mock writer 必须返回非空文本"
        assert cost == 0.001, f"mock cost 应为 0.001/调用，实际 {cost}"
        assert len(text) >= 1800, (
            f"mock writer 应返回接近 2000 字的章节（满足 call_with_length_budget 区间），"
            f"实际 {len(text)}"
        )

    def test_mock_provider_no_api_key_needed(self):
        """mock provider 不能读任何 api_key env（环境变量没设也不报错）。"""
        import os
        from engine.llm.router import LLMRouter
        # 删掉所有 API key env
        for k in ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
                  "KIMI_API_KEY", "MINIMAX_API_KEY", "CUSTOM_API_KEY"]:
            os.environ.pop(k, None)
        r = LLMRouter("test")
        r.routes["planner"] = ("mock", "mock-model")
        # 不抛异常 + 返回非空
        text, cost = r.call("planner", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text
        assert "Mock" in text or "mock" in text, (
            f"mock planner 应返回标记为 Mock 的内容：{text[:100]!r}"
        )

    def test_mock_provider_returns_schema_valid_json(self):
        """checker / tracker / outline 等 agent 的 mock 响应必须是合法 JSON。"""
        import json
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        for agent in ["tracker", "compliance", "checker_main", "outline"]:
            r.routes[agent] = ("mock", "mock-model")
            text, _ = r.call(agent, "sys", "user", max_tokens=4000, temperature=0.7)
            parsed = json.loads(text)  # 必须能 parse
            assert isinstance(parsed, dict), (
                f"mock {agent} 响应必须是 JSON dict，实际 {type(parsed).__name__}"
            )
            assert len(parsed) > 0, f"mock {agent} 响应不能是空 dict"

    def test_mock_provider_does_not_break_stats(self):
        """mock 调用应该正常累计 stats（不抛异常）。"""
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        r.routes["writer"] = ("mock", "mock-model")
        r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        stats = r.get_stats()
        assert stats["total_calls"] == 1
        assert abs(stats["total_cost_usd"] - 0.001) < 1e-6
        assert stats["by_agent"]["writer"]["calls"] == 1


class TestMockProviderAutoActivate:
    """历史背景（独立审查标记的中危点修复扩展）：
      之前 mock provider 只在 router.py 内显式设置 routes 才能用。
      CI / 单元测试 / demo 用户要"无需任何配置就让 engine 跑 mock"
      必须有 env 开关。

      本轮修复：NOVEL_ENGINE_MOCK=1 → LLMRouter 构造时自动 use_mock()
      把全部 9 个 agent routes 切到 mock provider（无需 API key）。
    """

    def test_env_var_triggers_use_mock(self, monkeypatch):
        """NOVEL_ENGINE_MOCK=1 → 构造 LLMRouter 后所有 routes 是 mock。"""
        monkeypatch.setenv("NOVEL_ENGINE_MOCK", "1")
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        for agent, route in r.routes.items():
            assert route[0] == "mock", (
                f"NOVEL_ENGINE_MOCK=1 后 agent '{agent}' 应指向 mock，实际 {route[0]!r}"
            )

    def test_explicit_use_mock_method(self):
        """不设 env，调用 r.use_mock() 也能切到 mock（用于运行时切换）。"""
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        assert r.routes["writer"][0] != "mock", "默认 routes 不应是 mock"
        r.use_mock()
        assert r.routes["writer"][0] == "mock", "显式 use_mock() 后应切到 mock"

    def test_no_env_no_api_key_still_raises(self):
        """不设 NOVEL_ENGINE_MOCK + 没 API key → 默认 routes 不应自动变 mock（保持原行为）。"""
        import os
        for k in ["NOVEL_ENGINE_MOCK", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "MINIMAX_API_KEY"]:
            os.environ.pop(k, None)
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        # 默认仍是真实 provider（除非显式 use_mock 或 NOVEL_ENGINE_MOCK=1）
        assert r.routes["writer"][0] != "mock", (
            "无 env 触发不应自动 mock（保留 opt-in 行为）"
        )


class TestMockProviderEndToEnd:
    """迭代 #1: 验证 mock 模式不仅单测过，真实构造 LLMRouter 时也起作用。

    历史背景：
      之前 mock provider 只在 router.py 内显式设置 routes 才能用。
      commit 6d6c07b 加了 NOVEL_ENGINE_MOCK=1 env 自动激活，但单测可能
      不能覆盖真实 import + 构造路径（mock path 可能只在测试 fixture 里）。
    """

    def test_llm_router_construction_with_mock_env(self):
        """设 NOVEL_ENGINE_MOCK=1 后 LLMRouter() 自动 use_mock() — 真实构造路径。"""
        import os
        os.environ["NOVEL_ENGINE_MOCK"] = "1"
        try:
            # 真 import + 构造（不走 mock 模块）
            from engine.llm.router import LLMRouter
            r = LLMRouter("test-end-to-end")
            # 9 个 agent 全部 mock
            assert r.routes["writer"][0] == "mock"
            assert r.routes["tracker"][0] == "mock"
            assert r.routes["orchestrator"][0] == "mock"
            # 真实 call() 调用走 mock 分支
            text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
            assert len(text) > 100, "mock writer 应返回长文本"
            assert cost == 0.001
        finally:
            os.environ.pop("NOVEL_ENGINE_MOCK", None)


class TestLlmRouterMiniMaxReasoningContent:
    """迭代 #32: _minimax 之前对 reasoning_content 存在但 content 为空的响应
    有死代码 fallback（line 456-458 重新赋 msg.get("content", "") 还是空），
    导致 M3 思考模式被意外开启时静默返回空文本，caller 把空文本当成正常
    生成继续 pipeline。

    修法：检测到 reasoning_content 非空 + content 空时直接 raise ValueError
    让配置 bug 暴露（MINIMAX_BASE_URL 可能被覆盖到旧版 endpoint）。

    本测试锁死：mock httpx 返回 {"content": "", "reasoning_content": "..."}，
    验证 _minimax raise ValueError 而不是返回空字符串。
    """
    def test_minimax_raises_on_reasoning_content_with_empty_content(self, monkeypatch):
        """MiniMax M3 思考模式被意外开启 → 必须 raise ValueError。"""
        import httpx
        from engine.llm import router as router_mod
        from engine.llm.router import LLMRouter

        # 准备一个 fake response：content 空 + reasoning_content 非空
        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": "",
                            "reasoning_content": "用户问的是测试，让我先思考一下...",
                        }
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
        # 设置 MINIMAX_API_KEY
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

        r = LLMRouter("test")
        r.routes["writer"] = ("minimax", "MiniMax-M3")

        # mock httpx.Client.post 返回 fake response
        class FakeClient:
            def __init__(self, *a, **kw): pass
            def post(self, *a, **kw):
                return FakeResp()
        monkeypatch.setattr(router_mod, "_get_client", lambda timeout=120: FakeClient())
        monkeypatch.setattr(router_mod, "_get_proxied_client", lambda *a, **kw: FakeClient())

        # 必须 raise ValueError（之前的 bug 是返回空 text）
        with pytest.raises(ValueError, match="reasoning_content"):
            r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)

    def test_minimax_returns_content_normally(self, monkeypatch):
        """正常 content 响应（非空）→ 正常返回。"""
        import httpx
        from engine.llm import router as router_mod
        from engine.llm.router import LLMRouter

        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": "正常回答的章节内容",
                            # 没 reasoning_content
                        }
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

        r = LLMRouter("test")
        r.routes["writer"] = ("minimax", "MiniMax-M3")

        class FakeClient:
            def __init__(self, *a, **kw): pass
            def post(self, *a, **kw):
                return FakeResp()
        monkeypatch.setattr(router_mod, "_get_client", lambda timeout=120: FakeClient())
        monkeypatch.setattr(router_mod, "_get_proxied_client", lambda *a, **kw: FakeClient())

        # 正常返回（不 raise）
        text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text == "正常回答的章节内容"
        assert cost > 0

    def test_minimax_empty_content_no_reasoning_falls_back(self, monkeypatch):
        """content 空 + 无 reasoning_content → 走最底部兜底（text 字段 / reply 字段），
        不 raise。"""
        from engine.llm import router as router_mod
        from engine.llm.router import LLMRouter

        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": "",
                            # 没 reasoning_content
                        },
                        "text": "M2 系列 fallback text 字段",
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

        r = LLMRouter("test")
        r.routes["writer"] = ("minimax", "MiniMax-M3")

        class FakeClient:
            def __init__(self, *a, **kw): pass
            def post(self, *a, **kw):
                return FakeResp()
        monkeypatch.setattr(router_mod, "_get_client", lambda timeout=120: FakeClient())
        monkeypatch.setattr(router_mod, "_get_proxied_client", lambda *a, **kw: FakeClient())

        # 不 raise，走兜底拿 text 字段
        text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text == "M2 系列 fallback text 字段"


class TestLlmRouterDecryptFailureLogging:
    """迭代 #38: engine/llm_router.py load_routes 之前 except Exception
    静默吞解密错误（MASTER_KEY 变了 → key=""），无 log。

    后果：用户改 MASTER_KEY env 后所有 LLM 不可用，错误日志里没任何线索，
    排查只能从 DB 翻 Provider.api_key_encrypted 自己 decode。

    修法：log warning 告诉用户哪个 provider 解密失败。
    本测试锁死：mock decrypt_api_key 抛异常 → load_routes 必须 log warning。
    """
    def test_load_routes_logs_warning_on_decrypt_failure(self, caplog):
        """decrypt_api_key 抛异常 → load_routes 必须 log warning（不静默）。"""
        import logging
        from engine import llm_router
        from engine.llm_router import LLMRouter as BridgeLLMRouter
        from app.database import SessionLocal
        from app.models import Provider, RoleAssignment
        import secrets

        # mock decrypt_api_key 抛异常
        def fake_decrypt(ciphertext):
            raise ValueError("simulated MASTER_KEY mismatch")
        import app.security
        original_decrypt = app.security.decrypt_api_key
        app.security.decrypt_api_key = fake_decrypt
        # llm_router 已经 import 了 decrypt_api_key 的引用，需要 patch 它
        llm_router.decrypt_api_key = fake_decrypt

        try:
            # 准备 project + provider + role assignment
            provider_id = f"test-decrypt-{secrets.token_hex(8)}"
            role_key = f"test-role-{secrets.token_hex(4)}"
            db = SessionLocal()
            try:
                p = Provider(
                    id=provider_id,
                    name="test-decrypt",
                    provider_type="anthropic",
                    api_key_encrypted="encrypted-blob-fake",
                    api_key_suffix="abcd",
                    default_model="claude-test",
                )
                db.add(p)
                ra = RoleAssignment(role_key=role_key, provider_id=provider_id)
                db.add(ra)
                db.commit()
            finally:
                db.close()

            r = BridgeLLMRouter("test-decrypt-novel")
            with caplog.at_level(logging.WARNING, logger="novel_ai.llm_router"):
                r.load_routes()
            # 关键断言：必须 log warning（不能静默）
            warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert len(warnings) >= 1, (
                f"decrypt 失败时必须 log warning，实际 log："
                f"{[(r.levelname, r.message) for r in caplog.records]}"
            )
            # warning 信息应含 provider id 或 role_key
            assert any(role_key in r.message or provider_id in r.message for r in warnings), (
                f"warning 信息应含 provider/role 标识，实际：{[r.message for r in warnings]}"
            )
        finally:
            app.security.decrypt_api_key = original_decrypt
            llm_router.decrypt_api_key = original_decrypt
            # 清理
            db = SessionLocal()
            try:
                db.query(RoleAssignment).filter_by(role_key=role_key).delete()
                db.query(Provider).filter_by(id=provider_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_load_routes_source_logs_on_decrypt_failure(self):
        """源码级锁死：load_routes 必须 log.warning。"""
        from pathlib import Path
        router_py = Path(BACKEND_ROOT) / "engine" / "llm_router.py"
        content = router_py.read_text(encoding="utf-8")
        # 找 load_routes 函数体（兼容多行签名 + 缩进）
        lines = content.splitlines()
        body_start = None
        for i, line in enumerate(lines):
            if "def load_routes" in line:
                body_start = i + 1
                break
        assert body_start is not None, "找不到 load_routes"
        body_lines = []
        for line in lines[body_start:]:
            if line.startswith("def ") or line.startswith("    def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        assert "log.warning" in body, (
            "load_routes 必须 log.warning（之前静默吞 decrypt 错误无 log）"
        )
        assert "decrypt" in body.lower(), (
            "load_routes 体内应有 decrypt 相关处理（不能是死代码）"
        )


class TestProxyApplied:
    """迭代 #46: 之前 _get_proxied_client 读 `_proxy_mounts.get(provider)`
    期望拿到 URL 字符串，但 `_proxy_mounts` 实际是 dict[str, httpx.Client]
    （缓存 httpx.Client）。真 URL 在 `_PROVIDER_PROXY`（set_proxy_map 写入）。

    后果：用户在 Provider 表里勾选 needs_proxy + 设 DEEPSEEK_PROXY env
    → 期望 deepseek 流量走代理；实际 _get_proxied_client 拿到 None
    → 返回 _get_client(120)（无代理）→ GFW 区域用户无法调用 deepseek。

    修法：从 _PROVIDER_PROXY 读 URL。

    锁死：set_proxy_map 后 _get_proxied_client 必须返回 proxy-mounted client
    （_proxy_mounts 缓存里有以 (provider, proxy_url, timeout) 为 key 的 Client）。
    """
    def test_proxy_applied_after_set_proxy_map(self):
        from engine.llm import router as router_mod

        # 重置模块级缓存 + proxy map（避免其他测试污染）
        router_mod._proxy_mounts.clear()
        router_mod._PROVIDER_PROXY.clear()

        # 配置 deepseek 走代理
        router_mod.LLMRouter().set_proxy_map({"deepseek": "http://127.0.0.1:7890"})

        # 调 _get_proxied_client — 必须返回挂代理的 Client
        client = router_mod._get_proxied_client(
            "deepseek", "https://api.deepseek.com/v1/chat/completions", 120,
        )
        assert client is not None, "set_proxy_map 后 _get_proxied_client 必须返回 client"

        # _proxy_mounts 缓存里必须有该 client
        cached_keys = [k for k in router_mod._proxy_mounts.keys() if isinstance(k, tuple)]
        assert any(
            k[0] == "deepseek" and k[1] == "http://127.0.0.1:7890" and k[2] == 120
            for k in cached_keys
        ), f"proxy 缓存里必须有 (deepseek, http://127.0.0.1:7890, 120)，实际 {list(router_mod._proxy_mounts.keys())}"

    def test_no_proxy_returns_regular_client(self):
        from engine.llm import router as router_mod

        router_mod._proxy_mounts.clear()
        router_mod._PROVIDER_PROXY.clear()

        # 不调 set_proxy_map — _PROVIDER_PROXY 空
        client = router_mod._get_proxied_client(
            "anthropic", "https://api.anthropic.com/v1/messages", 120,
        )
        assert client is not None, "无 proxy 时必须返回 client"
        cached_tuples = [k for k in router_mod._proxy_mounts.keys() if isinstance(k, tuple)]
        assert len(cached_tuples) == 0, \
            f"无 proxy 时不应有 cached tuple key，实际 {cached_tuples}"

    def test_proxy_cached_across_calls(self):
        from engine.llm import router as router_mod

        router_mod._proxy_mounts.clear()
        router_mod._PROVIDER_PROXY.clear()
        router_mod.LLMRouter().set_proxy_map({"kimi": "http://127.0.0.1:7890"})

        c1 = router_mod._get_proxied_client("kimi", "https://api.moonshot.cn/v1/chat", 120)
        c2 = router_mod._get_proxied_client("kimi", "https://api.moonshot.cn/v1/chat", 120)
        assert c1 is c2, "第二次调必须返回同一个 cached Client（避免每次新建）"

    def test_proxy_url_source_is_provider_proxy(self):
        import inspect
        from engine.llm import router as router_mod
        src = inspect.getsource(router_mod._get_proxied_client)
        # 去掉 docstring（避免「之前 _proxy_mounts.get(provider)」这种历史说明误匹配）
        code_lines = []
        in_docstring = False
        for line in src.split("\n"):
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                count = stripped.count('"""') + stripped.count("'''")
                if count == 1:
                    in_docstring = not in_docstring
                    continue
                elif count == 2:
                    continue
                else:
                    in_docstring = not in_docstring
                    continue
            if in_docstring or stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_src = "\n".join(code_lines)
        assert "_PROVIDER_PROXY.get(provider)" in code_src, \
            "_get_proxied_client 必须从 _PROVIDER_PROXY.get(provider) 读 URL（fix #46）"
        assert "_proxy_mounts.get(provider)" not in code_src, \
            "_get_proxied_client 不能从 _proxy_mounts.get(provider) 读 URL（fix #46 之前 bug）"


class TestAnthropicProxyApplied:
    """迭代 #51: _anthropic 之前用 Anthropic() 直接调用，没传 http_client。
    即使 _PROVIDER_PROXY["anthropic"] 配了，proxy 永远不生效。
    后果：GFW 区域用户勾选 anthropic.needs_proxy + 设 ANTHROPIC_PROXY
    → anthropic API 直连 → 超时 / 失败。

    修法：检测 _PROVIDER_PROXY.get("anthropic")，有就构造 httpx.Client(proxy=...)
    作为 http_client 参数传给 Anthropic SDK。
    """
    def test_anthropic_passes_http_client_when_proxy_configured(self):
        import inspect
        from engine.llm import router as router_mod
        # _anthropic 是 LLMRouter 类方法，不是模块级函数
        src = inspect.getsource(router_mod.LLMRouter._anthropic)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert '"http_client"' in code_src or "'http_client'" in code_src, \
            "_anthropic 必须用 'http_client' 参数把 httpx.Client 传给 Anthropic SDK（fix #51）"
        assert '_PROVIDER_PROXY.get("anthropic")' in code_src, \
            "_anthropic 必须从 _PROVIDER_PROXY.get('anthropic') 读 proxy URL"

    def test_anthropic_proxy_actually_constructed(self, monkeypatch):
        from unittest.mock import patch, MagicMock
        import httpx
        from engine.llm import router as router_mod

        router_mod._PROVIDER_PROXY.clear()
        router_mod.LLMRouter().set_proxy_map({"anthropic": "http://127.0.0.1:7890"})

        captured = {}
        def fake_anthropic_ctor(**kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.messages.create.return_value = MagicMock(
                content=[MagicMock(text="hi")],
                usage=MagicMock(input_tokens=10, output_tokens=5,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0),
            )
            return m
        with patch.object(router_mod, "Anthropic", side_effect=fake_anthropic_ctor):
            r = router_mod.LLMRouter()
            r._anthropic("checker_main", "sys", "user", "claude-sonnet-4-5",
                         max_tokens=100, temperature=0.5)
        assert "http_client" in captured, \
            f"_anthropic 必须传 http_client 参数，实际 kwargs: {list(captured.keys())}"
        assert isinstance(captured["http_client"], httpx.Client), \
            f"http_client 必须是 httpx.Client 实例，实际 {type(captured['http_client'])}"

    def test_anthropic_no_proxy_no_http_client(self, monkeypatch):
        from unittest.mock import patch, MagicMock
        from engine.llm import router as router_mod

        router_mod._PROVIDER_PROXY.clear()

        captured = {}
        def fake_anthropic_ctor(**kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.messages.create.return_value = MagicMock(
                content=[MagicMock(text="hi")],
                usage=MagicMock(input_tokens=10, output_tokens=5,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0),
            )
            return m
        with patch.object(router_mod, "Anthropic", side_effect=fake_anthropic_ctor):
            r = router_mod.LLMRouter()
            r._anthropic("checker_main", "sys", "user", "claude-sonnet-4-5",
                         max_tokens=100, temperature=0.5)
        assert "http_client" not in captured, \
            f"没配 proxy 时不应传 http_client，实际 kwargs: {captured}"


class TestMinimaxEndpointUpdated:
    """迭代 #52: app/config.py 的 minimax_api_base 默认是旧版 endpoint
    api.minimax.chat（router.py iter #32 已切到 api.minimaxi.com）。

    后果：用户没设 NOVEL_MINIMAX_API_BASE env 时，app/llm_router.py
    通过 settings.minimax_api_base 拿旧 endpoint → 调用 404 / 401。

    锁死：config.py 的 minimax_api_base 默认必须跟 router.py 的
    MINIMAX_BASE_URL fallback 一致（api.minimaxi.com）。
    """
    def test_config_minimax_default_uses_new_endpoint(self):
        from app.config import settings
        assert "minimaxi.com" in settings.minimax_api_base, \
            f"config.minimax_api_base 默认必须用新 endpoint api.minimaxi.com，实际 {settings.minimax_api_base}"

    def test_config_minimax_no_old_endpoint_default(self):
        from app.config import settings
        assert "minimax.chat" not in settings.minimax_api_base, \
            f"config.minimax_api_base 不能默认旧 endpoint api.minimax.chat（404），实际 {settings.minimax_api_base}"

    def test_config_minimax_default_model_is_m3(self):
        from app.config import settings
        assert "M3" in settings.minimax_model or "minimax" in settings.minimax_model.lower(), \
            f"config.minimax_model 默认应指向当前在用的 model，实际 {settings.minimax_model}"


class TestLlmClientRetryCatchesAll:
    """迭代 #62: app/llm_client.py:71 之前只 catch KeyError — IndexError
    （choices 空列表）和 TypeError（message 是 None）会跳出重试循环。
    修法：扩 catch 列表。
    """
    def test_llm_client_catches_index_error(self):
        """LLM 返回 {\"choices\": []} 必须走重试，不是直接抛 IndexError。"""
        from unittest.mock import patch, AsyncMock, MagicMock
        from app import llm_client as lc_mod
        import httpx

        # mock resolve_provider 返回 fake provider
        fake_cfg = MagicMock()
        fake_cfg.provider = "deepseek"
        fake_cfg.api_base = "https://api.deepseek.com/v1"
        fake_cfg.api_key = "sk-fake"
        fake_cfg.model = "deepseek-chat"

        # 第一次返回 choices=[]（IndexError），第二次成功
        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json = MagicMock(return_value={"choices": []})
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"ok": true}'}}]
        })
        # 使用 AsyncMock 让两次调用返回不同值
        async def post_side_effect(*args, **kwargs):
            return empty_resp if post_side_effect.call_count == 0 else ok_resp
        post_side_effect.call_count = 0
        async def track(*args, **kwargs):
            post_side_effect.call_count += 1
            if post_side_effect.call_count == 1:
                return empty_resp
            return ok_resp

        with patch.object(lc_mod, "resolve_provider", return_value=fake_cfg), \
             patch.object(lc_mod, "_build_httpx_client") as mock_client:
            # 构造 AsyncClient that 走 __aenter__ 返回 .post side_effect
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=None)
            mock_inst.post = track
            mock_client.return_value = mock_inst

            import asyncio
            result = asyncio.run(lc_mod.call_llm_json("structured_logic", "sys", "user"))
        assert result == {"ok": True}, \
            f"IndexError 必须被重试吞掉，第二次成功返回 dict，实际 {result}"

    def test_llm_client_catches_type_error(self):
        """LLM 返回 {\"choices\": [{\"message\": null}]} 必须走重试。"""
        from unittest.mock import patch, AsyncMock, MagicMock
        from app import llm_client as lc_mod

        fake_cfg = MagicMock()
        fake_cfg.provider = "deepseek"
        fake_cfg.api_base = "https://api.deepseek.com/v1"
        fake_cfg.api_key = "sk-fake"
        fake_cfg.model = "deepseek-chat"

        type_err_resp = MagicMock()
        type_err_resp.raise_for_status = MagicMock()
        type_err_resp.json = MagicMock(return_value={
            "choices": [{"message": None}]  # None["content"] → TypeError
        })
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"ok": true}'}}]
        })

        with patch.object(lc_mod, "resolve_provider", return_value=fake_cfg), \
             patch.object(lc_mod, "_build_httpx_client") as mock_client:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=None)
            call_count = [0]
            async def track(*args, **kwargs):
                call_count[0] += 1
                return type_err_resp if call_count[0] == 1 else ok_resp
            mock_inst.post = track
            mock_client.return_value = mock_inst
            import asyncio
            result = asyncio.run(lc_mod.call_llm_json("structured_logic", "sys", "user"))
        assert result == {"ok": True}, \
            f"TypeError 必须被重试吞掉，实际 {result}"


class TestParseLlmJsonResponseAllStrategiesFail:
    """迭代 #80：engine/utils.py.parse_llm_json_response 之前 3 个 JSON parse
    策略（直接 parse / 平衡 JSON 提取 / 删尾逗号再 parse）全失败时静默
    return default，无任何日志——caller 拿 default 不知道 LLM 实际返回了
    什么。fake-pass 同型问题：orchestrator 走「校验 → 标 PASS」流程
    的某些 agent 可能用 default 假装解析成功。

    修法：3 个策略全失败时 log.warning 带 resp[:200] + strategy 标识
    让运维看到「LLM 返回了非 JSON」信号，行为仍 return default 不变。
    """
    def test_parse_llm_json_response_logs_on_total_failure(self):
        """源码扫描：parse_llm_json_response 末尾 parsed is None 时必须 log（#80 — fake-pass 反模式）。

        函数里有两个 `if parsed is None:` 块：
          - 中间段（每个策略失败后判断要不要继续尝试）
          - **末尾 fallback**（3 个策略全部失败 → log + return default）
        必须有**末尾**那个 log，中间段不一定需要。
        """
        import inspect
        from engine import utils as engine_utils
        src = inspect.getsource(engine_utils.parse_llm_json_response)
        # 找最后一个 `if parsed is None:`（terminal fallback）
        idx = src.rfind("if parsed is None:")
        assert idx != -1, "parse_llm_json_response 必须有 `if parsed is None` fallback"
        chunk = src[idx:idx + 500]
        assert "log" in chunk.lower(), (
            f"parse_llm_json_response 末尾 fallback 块必须 log（#80 — fake-pass 反模式）\n"
            f"chunk:\n{chunk}"
        )
        # 也确保有 return default（行为不变）
        assert "return default" in chunk, \
            f"fallback 块必须 return default 但 log 同步，chunk:\n{chunk}"

    def test_behavioral_total_failure_returns_default_and_logs(self, caplog):
        """行为测试：3 个策略全失败时返回 default + 记录 warning。

        模拟 LLM 返回纯文本（不是 JSON）→ parse_llm_json_response 应该
        log.warning + return default。这正是 audit 担心的 silent fake-pass 场景。
        """
        import logging
        from engine.utils import parse_llm_json_response
        with caplog.at_level(logging.WARNING, logger="novel_ai.utils"):
            # 纯文本 "this is not json" 会被 3 个策略全部拒绝
            result = parse_llm_json_response("this is just plain text, no JSON here", {"expected": "dict"})
        # 行为：返回 default
        assert result == {"expected": "dict"}, \
            f"全部失败应返回 default，实际 {result}"
        # 关键：log warning 必须有
        warn_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warn_records, (
            "全部 parse 策略失败时必须有 log（#80 — 之前静默 fallback）"
            f"实际 caplog: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        # log 应包含 resp 前 200 字符（让运维看到 LLM 实际返回了什么）
        msgs = [r.getMessage() for r in warn_records]
        assert any("plain text" in m for m in msgs), \
            f"log 应包含 LLM 返回内容（截断 200 字符），实际 messages: {msgs}"

    def test_behavioral_partial_failure_no_log_when_strategy_works(self, caplog):
        """行为测试（反向）：某个策略成功时不应该 trigger 全部失败的 log。

        直接的合法 JSON 应该 parse 成功，不走 "all failed" 分支。
        """
        import logging
        from engine.utils import parse_llm_json_response
        with caplog.at_level(logging.WARNING, logger="novel_ai.utils"):
            result = parse_llm_json_response('{"title": "good"}', {"expected": "dict"})
        # 行为：返回 parsed dict
        assert isinstance(result, dict)
        assert result.get("title") == "good"
        # 反向保证：合法 JSON 不触发 "all strategies failed" log
        warn_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        all_failed_msgs = [r.getMessage() for r in warn_records
                           if "全部" in r.getMessage() or "全失败" in r.getMessage()]
        assert not all_failed_msgs, \
            f"合法 JSON 不应触发 'all strategies failed' log，实际: {all_failed_msgs}"


class TestLLMRouterInstallMockContract:
    """锁死 engine.llm_router.LLMRouter.install() 在 mock 模式下的行为。

    历史 bug (iter #84)：
      install() 内部跑 load_routes() + configure(routes=...)，而 configure
      是 routes.update(...) — 会把 LLMRouter.__init__ 设的 mock routes
      全部覆盖回 DB RoleAssignment 的真实 provider。
      后果：NOVEL_ENGINE_MOCK=1 启动时，subprocess 应当全走 mock，但
      因为 configure.update() 覆盖了 mock，planner 真去调 MiniMax API
      → ValueError('MINIMAX_API_KEY 未设置')。
      修法：install() 入口检查 NOVEL_ENGINE_MOCK=1 → 跳过 DB 加载，
      让 __init__ 设的 mock routes 保留。
    """

    def test_install_short_circuits_on_mock_env(self):
        """NOVEL_ENGINE_MOCK=1 时 install() 应跳过 load_routes / configure。"""
        from engine.llm_router import LLMRouter
        import inspect
        src = inspect.getsource(LLMRouter.install)
        # 必须有早退路径
        assert "NOVEL_ENGINE_MOCK" in src, (
            "LLMRouter.install() 必须读 NOVEL_ENGINE_MOCK env；缺失等于让 mock 模式 "
            "继续调 DB → 真去发请求"
        )

    def test_engine_router_init_applies_mock(self, monkeypatch):
        """engine.llm.router.LLMRouter.__init__ 在 NOVEL_ENGINE_MOCK=1 时切 mock routes。

        这是 install() short-circuit 的前置依赖：
          install() 让 __init__ 设的 routes 留下来, 所以 __init__ 必须真的切。
        """
        monkeypatch.setenv("NOVEL_ENGINE_MOCK", "1")
        from engine.llm.router import LLMRouter as _EngineRouter
        r = _EngineRouter()
        # __init__ 在 mock 模式应当把全部 routes 切到 ('mock', 'mock-model')
        for agent, (provider, _model) in r.routes.items():
            assert provider == "mock", (
                f"mock 模式下 agent={agent!r} 的 provider 应当是 'mock', 实际 {provider!r}"
            )
