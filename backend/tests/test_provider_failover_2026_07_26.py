"""test_provider_failover_2026_07_26.py

架构审视 — Provider 故障分类 + 故障转移。

背景（docs/wiki/07-Real-LLM-Testing.md §4）：
30 章实测里 MiniMax 的 Token Plan 限速（base_resp.status_code=2062）在 30 章后
"大概率撞"。原实现有两个缺口，叠加起来让长跑必断：

1. **MiniMax 业务错误码从不解析**：额度耗尽时 MiniMax 返回 HTTP 200 +
   空 choices，代码抛 `ValueError("MiniMax 返回无 choices")` —— 类型上无法与
   "模型输出异常"区分。
2. **没有任何 provider 级故障转移**：`_post_with_retry` 只在单个 provider 内重试
   6 次，4xx 直接 fail-fast，然后走 orchestrator 的
   "3-retry-then-placeholder" 兜底 —— 写出占位章节，正是质量债的来源。

修法：错误分类（quota / auth / 其它）+ call() 里按 fallback_chain 换 provider。

关键约束：**绝不把 mock 当备选**。那会把"调用失败"伪装成"生成成功"，
写出一堆假章节，违反 CLAUDE.md「禁止静默吞掉调用失败」。
"""
from __future__ import annotations

import httpx
import pytest

from engine.llm.router import (
    LLMAuthError,
    LLMProviderError,
    LLMQuotaError,
    LLMRouter,
    classify_http_error,
    raise_for_minimax_base_resp,
)


# ─── 1. 错误分类 ─────────────────────────

@pytest.mark.parametrize("code", [401, 403])
def test_auth_status_codes_classified(code):
    err = classify_http_error("minimax", code, "no permission")
    assert isinstance(err, LLMAuthError)
    assert err.kind == "auth"
    assert err.provider == "minimax"


@pytest.mark.parametrize("code", [402, 429])
def test_quota_status_codes_classified(code):
    err = classify_http_error("deepseek", code, "rate limited")
    assert isinstance(err, LLMQuotaError)
    assert err.kind == "quota"


@pytest.mark.parametrize("code", [400, 404, 422])
def test_other_4xx_is_plain_provider_error(code):
    err = classify_http_error("kimi", code, "bad request")
    assert isinstance(err, LLMProviderError)
    assert not isinstance(err, (LLMQuotaError, LLMAuthError))


def test_provider_errors_are_valueerror_for_backward_compat():
    """既有调用点按 ValueError 兜底，分类必须是纯增量、不破坏它们。"""
    assert issubclass(LLMProviderError, ValueError)
    assert issubclass(LLMQuotaError, ValueError)
    assert issubclass(LLMAuthError, ValueError)


def test_error_message_carries_provider_and_kind():
    err = classify_http_error("minimax", 429, "too many")
    assert "minimax" in str(err) and "quota" in str(err)


# ─── 2. MiniMax base_resp ─────────────────────────

@pytest.mark.parametrize("code", [1002, 1008, 2062])
def test_minimax_quota_codes_raise_quota_error(code):
    """2062 = Token Plan 限速，30 章实测必撞的那个。"""
    with pytest.raises(LLMQuotaError) as ei:
        raise_for_minimax_base_resp({"base_resp": {"status_code": code,
                                                   "status_msg": "rate limit"}})
    assert str(code) in str(ei.value)


def test_minimax_auth_code_raises_auth_error():
    with pytest.raises(LLMAuthError):
        raise_for_minimax_base_resp({"base_resp": {"status_code": 1004}})


def test_minimax_unknown_nonzero_code_raises_provider_error():
    with pytest.raises(LLMProviderError) as ei:
        raise_for_minimax_base_resp({"base_resp": {"status_code": 2013,
                                                   "status_msg": "bad input"}})
    assert not isinstance(ei.value, (LLMQuotaError, LLMAuthError))


@pytest.mark.parametrize("data", [
    {},
    {"base_resp": {}},
    {"base_resp": {"status_code": 0}},
    {"base_resp": {"status_code": "0"}},   # 非 int 不误判
    {"base_resp": None},
])
def test_minimax_success_shapes_do_not_raise(data):
    raise_for_minimax_base_resp(data)


# ─── 3. 故障转移 ─────────────────────────

def _router_with(primary, chain, keys):
    r = LLMRouter(project_id="t")
    r.routes["writer"] = primary
    r.api_keys.update(keys)
    r.set_fallback_chain(chain)
    return r


def _stub(router, outcomes: dict):
    """把每个 provider 方法替换成"要么抛给定异常、要么返回给定文本"。"""
    calls = []

    def make(pname):
        def fn(agent, system, user, model, max_tokens, temperature, *a, **kw):
            calls.append(pname)
            out = outcomes[pname]
            if isinstance(out, Exception):
                raise out
            return out, 0.001
        return fn

    for pname in ("anthropic", "deepseek", "gemini", "kimi", "minimax",
                  "custom", "mock"):
        setattr(router, f"_{pname}", make(pname))
    return calls


def test_falls_over_to_next_provider_on_quota():
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("minimax", "MiniMax-M3"), ("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "deepseek": "k2"})
    calls = _stub(r, {"minimax": LLMQuotaError("minimax", "2062"),
                      "deepseek": "章节正文"})
    text, cost = r.call("writer", "sys", "usr")
    assert text == "章节正文"
    assert calls == ["minimax", "deepseek"]


def test_failover_is_recorded_in_stats():
    """静默换 provider 会让『风格突然变了』变成无头案 —— 必须留痕。"""
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "deepseek": "k2"})
    _stub(r, {"minimax": LLMQuotaError("minimax", "2062"), "deepseek": "ok"})
    r.call("writer", "s", "u")
    fo = r.get_stats()["failovers"]
    assert fo == [{"agent": "writer", "from": "minimax", "to": "deepseek"}]


def test_no_failover_recorded_when_primary_succeeds():
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "deepseek": "k2"})
    _stub(r, {"minimax": "ok"})
    r.call("writer", "s", "u")
    assert "failovers" not in r.get_stats()


def test_http_errors_also_trigger_failover():
    """重试 6 次后仍是 5xx（HTTPStatusError）同样该换一家。"""
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "deepseek": "k2"})
    boom = httpx.ConnectError("network down")
    calls = _stub(r, {"minimax": boom, "deepseek": "ok"})
    assert r.call("writer", "s", "u")[0] == "ok"
    assert calls == ["minimax", "deepseek"]


def test_raises_when_all_providers_fail():
    """全部失败必须抛出，不能返回空文本让下游当成功。"""
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "deepseek": "k2"})
    _stub(r, {"minimax": LLMQuotaError("minimax", "2062"),
              "deepseek": LLMQuotaError("deepseek", "429")})
    with pytest.raises(LLMProviderError):
        r.call("writer", "s", "u")


def test_mock_is_never_used_as_fallback():
    """核心安全约束：mock 兜底会把失败伪装成成功，写出假章节。"""
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("mock", "mock")], {"minimax": "k1", "mock": "x"})
    calls = _stub(r, {"minimax": LLMQuotaError("minimax", "2062"), "mock": "假章节"})
    with pytest.raises(LLMQuotaError):
        r.call("writer", "s", "u")
    assert calls == ["minimax"]
    assert "mock" not in [p for p, _ in r.fallback_chain]


def test_providers_without_key_are_skipped():
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("kimi", "moonshot-v1"), ("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "kimi": "", "deepseek": "k2"})
    calls = _stub(r, {"minimax": LLMQuotaError("minimax", "x"),
                      "kimi": "不该被调用", "deepseek": "ok"})
    assert r.call("writer", "s", "u")[0] == "ok"
    assert calls == ["minimax", "deepseek"]


def test_primary_is_not_retried_as_fallback():
    """主 provider 出现在链里时不得被重复尝试。"""
    r = _router_with(("minimax", "MiniMax-M3"),
                     [("minimax", "MiniMax-M3"), ("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "deepseek": "k2"})
    calls = _stub(r, {"minimax": LLMQuotaError("minimax", "x"), "deepseek": "ok"})
    r.call("writer", "s", "u")
    assert calls.count("minimax") == 1


def test_empty_chain_preserves_original_fail_fast_behaviour():
    """不配链时行为与改动前一致：主 provider 失败就抛。"""
    r = _router_with(("minimax", "MiniMax-M3"), [], {"minimax": "k1"})
    calls = _stub(r, {"minimax": LLMQuotaError("minimax", "x")})
    with pytest.raises(LLMQuotaError):
        r.call("writer", "s", "u")
    assert calls == ["minimax"]


def test_unknown_primary_provider_still_raises_valueerror():
    r = _router_with(("nope", "m"), [], {})
    with pytest.raises(ValueError, match="未知 Provider"):
        r.call("writer", "s", "u")


def test_override_provider_participates_in_failover():
    r = _router_with(("anthropic", "claude"), [("deepseek", "deepseek-chat")],
                     {"minimax": "k1", "deepseek": "k2"})
    calls = _stub(r, {"minimax": LLMQuotaError("minimax", "x"), "deepseek": "ok"})
    assert r.call("writer", "s", "u", override_provider="minimax")[0] == "ok"
    assert calls == ["minimax", "deepseek"]


def test_set_fallback_chain_filters_mock_and_blanks():
    r = LLMRouter(project_id="t")
    r.set_fallback_chain([("mock", "m"), ("", "x"), ("deepseek", "deepseek-chat")])
    assert r.fallback_chain == [("deepseek", "deepseek-chat")]
