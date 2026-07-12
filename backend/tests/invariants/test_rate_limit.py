"""rate_limit/ — Phase 3 测试拆分

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

class TestRateLimitMiddleware:
    """历史背景（独立审查标记的范围外项）：
      当前无任何速率限制 → 攻击者用脚本刷 /bridge/run 会触发昂贵 LLM 调用
      （每次 $0.01-$0.10）→ 钱包爆掉。

      本轮修复：app.middleware.rate_limit.RateLimitMiddleware
        - 内存滑动窗口，默认 60 次/分钟/IP
        - 仅写端点限速（GET / OPTIONS / HEAD / 读路径不受限）
        - 通过 RATE_LIMIT_PER_MINUTE env 调整
        - 响应含 X-RateLimit-Limit / Remaining / Retry-After headers
    """

    def test_middleware_registered_in_main(self):
        """main.py 必须注册 RateLimitMiddleware。"""
        from pathlib import Path
        main_py = Path(REPO_ROOT) / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "RateLimitMiddleware" in content, (
            "main.py 必须注册 RateLimitMiddleware — "
            "否则攻击者能刷 /bridge/run 触发昂贵 LLM 调用"
        )
        assert "RATE_LIMIT_PER_MINUTE" in content, (
            "main.py 应支持 RATE_LIMIT_PER_MINUTE env"
        )

    def test_ip_rate_limiter_basic(self):
        """IPRateLimiter 基本逻辑：max+1 次后第 N+1 次被拒绝。"""
        from app.middleware.rate_limit import IPRateLimiter, reset_for_testing
        reset_for_testing()
        limiter = IPRateLimiter(max_per_minute=3)
        # 前 3 次允许
        assert limiter.is_allowed("1.2.3.4")
        assert limiter.is_allowed("1.2.3.4")
        assert limiter.is_allowed("1.2.3.4")
        # 第 4 次拒绝
        assert not limiter.is_allowed("1.2.3.4"), (
            "超出 max_per_minute 后必须拒绝"
        )
        # 不同 IP 独立计数
        assert limiter.is_allowed("5.6.7.8"), "不同 IP 必须独立计数"
        reset_for_testing()

    def test_write_endpoint_detection(self):
        """_is_write_endpoint 标记 /api/v1/ 下所有路径为潜在写（middleware 按 method 二次过滤）。

        注意：_is_write_endpoint 单看路径，middleware 在 dispatch 里再加一层
        GET/HEAD/OPTIONS 早退。所以这个 helper 是"路径是否是 /api/v1/ 下"。
        """
        from app.middleware.rate_limit import _is_write_endpoint
        # /api/v1/ 下所有路径（中间件按 method 二次过滤）
        assert _is_write_endpoint("/api/v1/projects/abc/bridge/run")
        assert _is_write_endpoint("/api/v1/projects/abc/worldbuild/start")
        assert _is_write_endpoint("/api/v1/providers/xyz")
        assert _is_write_endpoint("/api/v1/foreshadowings/123/status")
        assert _is_write_endpoint("/api/v1/projects/abc/bridge/status")  # GET 也标记
        # 豁免
        assert not _is_write_endpoint("/health")
        assert not _is_write_endpoint("/openapi.json")
        assert not _is_write_endpoint("/docs")

    def test_rate_limit_headers_in_response(self):
        """被限流的请求必须返回 429 + Retry-After / X-RateLimit-* headers。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.middleware.rate_limit import (
            _limiter, reset_for_testing,
        )
        # 强制设很低阈值
        from app.middleware import rate_limit
        rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=1)
        try:
            client = TestClient(app)
            # 第 1 次 POST /providers：允许（设很小的 body 可能 422，但不触发 rate limit）
            # 用 POST /providers 测（body 即使无效也先过 middleware）
            r1 = client.post("/api/v1/providers", json={})
            # 第 2 次：被限流
            r2 = client.post("/api/v1/providers", json={})
            # 注意：r1 可能是 422（body 校验），但 rate limit 已消耗
            # r2 必须是 429
            assert r2.status_code == 429, (
                f"第 2 次写请求应被限流（max=1），实际 {r2.status_code}"
            )
            assert "Retry-After" in r2.headers
            assert "X-RateLimit-Limit" in r2.headers
            assert r2.json().get("error") == "rate_limit_exceeded"
        finally:
            reset_for_testing()
            # 恢复模块级 limiter
            rate_limit._limiter = rate_limit.IPRateLimiter(
                max_per_minute=10000  # 测试环境高阈值
            )

    def test_allowed_proxies_parsing(self):
        """ALLOWED_PROXIES env 解析：单个 IP + CIDR + 无效值跳过。"""
        from app.middleware.rate_limit import _parse_allowed_proxies, RateLimitMiddleware
        # 重置缓存
        RateLimitMiddleware._allowed_proxies = None
        # 单个 IP
        import os
        os.environ["ALLOWED_PROXIES"] = "127.0.0.1,10.0.0.0/8,invalid_ip"
        nets = _parse_allowed_proxies()
        # invalid_ip 应被跳过
        assert len(nets) == 2, f"应解析 2 个有效 IP/CIDR（跳过 invalid），实际 {len(nets)}"
        os.environ.pop("ALLOWED_PROXIES", None)
        RateLimitMiddleware._allowed_proxies = None

    def test_ip_in_allowed_list_check(self):
        """_ip_in_allowed_list 正确判断 IP 是否在白名单。"""
        from app.middleware.rate_limit import _ip_in_allowed_list
        import ipaddress
        nets = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("10.0.0.0/8")]
        assert _ip_in_allowed_list("127.0.0.1", nets)
        assert _ip_in_allowed_list("10.5.6.7", nets)
        assert not _ip_in_allowed_list("8.8.8.8", nets)
        # 无效 IP 字符串
        assert not _ip_in_allowed_list("not_an_ip", nets)
        # 空白名单
        assert not _ip_in_allowed_list("127.0.0.1", [])


class TestRateLimitHeaderAccuracy:
    """最后 #18 迭代：rate_limit middleware 的 X-RateLimit-Remaining
    应该在被限流时返回 0 + Retry-After header。

    之前 TestRateLimitMiddleware 测了基本流程，没验证 header 准确性。
    生产监控可能根据 X-RateLimit-Remaining 做自动降级判断。
    """

    def test_rate_limited_response_has_zero_remaining(self):
        """被限流的响应 X-RateLimit-Remaining 必须 = 0。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.middleware import rate_limit
        from app.middleware.rate_limit import reset_for_testing

        rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=1)
        try:
            client = TestClient(app)
            client.post("/api/v1/providers", json={})  # 1
            r2 = client.post("/api/v1/providers", json={})  # 2 → 限流
            assert r2.status_code == 429
            assert r2.headers.get("X-RateLimit-Remaining") == "0", (
                f"被限流时 X-RateLimit-Remaining 必须 = 0，实际 {r2.headers.get('X-RateLimit-Remaining')}"
            )
        finally:
            reset_for_testing()
            rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=10000)

    def test_rate_limited_response_has_retry_after(self):
        """429 响应必须含 Retry-After header（让客户端知道多久重试）。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.middleware import rate_limit
        from app.middleware.rate_limit import reset_for_testing

        rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=1)
        try:
            client = TestClient(app)
            client.post("/api/v1/providers", json={})  # 1
            r2 = client.post("/api/v1/providers", json={})  # 2 → 限流
            assert r2.status_code == 429
            retry_after = r2.headers.get("Retry-After")
            assert retry_after is not None, "429 必须含 Retry-After header"
            assert int(retry_after) > 0, f"Retry-After 必须 > 0，实际 {retry_after}"
        finally:
            reset_for_testing()
            rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=10000)

    def test_health_endpoint_not_rate_limited(self):
        """/health 是健康检查（k8s livenessProbe 高频调用）不能被限流。"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        for _ in range(10):
            r = client.get("/health")
            assert r.status_code in (200, 503), (
                f"/health 不应被限流（GET 早退），实际 {r.status_code}"
            )


class TestRateLimitMemoryCleanup:
    """迭代 #81：审计报告 (2026-07-05) 指出 rate_limit.IPRateLimiter._buckets
    dict 永远不清空空 deque——长跑 N 个不同 IP 后 dict 里堆 N 个空 deque 占
    内存。审计原话：'单租户原型场景下可以忽略'。

    但加 1 行 lazy cleanup 就能解决——risk/reward 极高。修法：
      - is_allowed 拒绝路径上，如果清完过期时间戳 deque 空了，立刻从
        dict 中 pop 掉。lock 内 O(1)，无全扫。
      - 显式 cleanup_empty_buckets() 也提供（运维 / 测试可调）。
    """
    def test_is_allowed_opportunistic_sweep_cleans_stale(self, monkeypatch):
        """行为测试：每 1000 次请求触发 stale buckets sweep，把过期 bucket 删除。

        模拟：1 个 IP 填满 → 模拟时间过去 60s+ 让时间戳过期 → 触发 1000 次
        请求到达 sweep 点 → 期望 _buckets 不再保留这 IP。
        """
        import time as _t
        from app.middleware import rate_limit as rl_mod
        from app.middleware.rate_limit import IPRateLimiter
        limiter = IPRateLimiter(max_per_minute=2)
        # 填满窗口
        ok1 = limiter.is_allowed("1.2.3.4")
        ok2 = limiter.is_allowed("1.2.3.4")
        assert ok1 and ok2
        # 桶里有 2 个时间戳
        assert "1.2.3.4" in limiter._buckets
        assert len(limiter._buckets["1.2.3.4"]) == 2

        # 模拟时间过去 60s+（让时间戳过期）—— rl_mod.time.monotonic
        future = _t.monotonic() + 61
        monkeypatch.setattr(rl_mod.time, "monotonic", lambda: future)

        # 触发 1000 次请求（其中 999 次 no-op 加在别的 IP，最后 1 次在 1.2.3.4）
        # _request_count 从 0 开始累加，但之前已经加了 2 次（is_allowed × 2）。
        # 加 999 个 dummy 请求让 count = 1001 → 下次清理触发
        for i in range(999):
            limiter.is_allowed(f"other_{i}.{i}.{i}.{i}")
        # 现在 _request_count = 1001，最后一次 is_allowed 触发 sweep
        # 但 sweep 只清空时间戳全部过期的 bucket——"other_..." 都刚刚加进 dict，
        # 不会被清掉。1.2.3.4 桶里 2 个时间戳是 61s 前，应该被清掉。
        assert "1.2.3.4" not in limiter._buckets, (
            f"_buckets 仍保留过期的 IP 1.2.3.4（#81 sweep 未生效）"
            f"actual: {list(limiter._buckets.keys())[:5]}..."
        )

    def test_cleanup_stale_buckets_helper(self):
        """显式 cleanup_stale_buckets() 必须能清理 stale 桶并返回数量。

        直接构造一个 limiter，添加几个 IP，时间戳在窗口内——都不是 stale。
        然后构造场景：1 个桶有 1 个 OLD timestamp（> 60s ago），调 cleanup
        应清掉这个。"""
        import time as _t
        from app.middleware.rate_limit import IPRateLimiter
        limiter = IPRateLimiter(max_per_minute=10)
        # 直接构造一个 stale 桶（时间戳早就过去）
        old_ts = _t.monotonic() - 120  # 120s 前
        from collections import deque
        limiter._buckets["stale_ip"] = deque([old_ts])
        limiter._buckets["fresh_ip"] = deque([_t.monotonic()])
        cleaned = limiter.cleanup_stale_buckets()
        assert cleaned == 1, f"应清 1 个 stale（stale_ip），实际 {cleaned}"
        assert "stale_ip" not in limiter._buckets
        assert "fresh_ip" in limiter._buckets

    def test_module_has_cleanup_method(self):
        """源码扫描：IPRateLimiter 必须有 cleanup_stale_buckets 公开方法（#81 公开 API）。"""
        from app.middleware.rate_limit import IPRateLimiter
        assert hasattr(IPRateLimiter, "cleanup_stale_buckets"), (
            "IPRateLimiter 必须有 cleanup_stale_buckets 公开方法（#81 — "
            "运维 / 长跑周期任务可调，避免 dict 长期增长）"
        )

    def test_request_count_triggers_sweep(self, monkeypatch):
        """counter-based sweep 触发：累计 1000 次请求后，下次 is_allowed 触发 sweep。

        设置：filler_* 的时间戳用 OLD 时间（> window_seconds）→ 模拟这些桶已 stale，
        等 _request_count 累计到 1000，下一次 is_allowed 触发 sweep 时应清掉。
        """
        import time as _t
        from collections import deque
        from app.middleware import rate_limit as rl_mod
        from app.middleware.rate_limit import IPRateLimiter
        limiter = IPRateLimiter(max_per_minute=10)
        # 直接构造 1000 个 stale buckets（OLD 时间戳），_request_count 也设 999
        old_ts = _t.monotonic() - 120  # 120s 前
        for i in range(1000):
            limiter._buckets[f"stale_{i}"] = deque([old_ts])
        limiter._request_count = 999  # 下次 is_allowed 触发 sweep
        # 这次调用应触发 sweep——stale_* 全部应被清掉
        limiter.is_allowed("trigger_ip")
        # 1000 个 stale 桶全被清，剩下 trigger_ip
        assert len(limiter._buckets) < 1001, (
            f"sweep 应清掉 stale 桶，actual _buckets count: {len(limiter._buckets)}"
        )
        # 关键：stale_* 全没了
        stale_remaining = [k for k in limiter._buckets if k.startswith("stale_")]
        assert not stale_remaining, \
            f"stale 桶仍残留: {len(stale_remaining)} 个（{stale_remaining[:3]}...）"
