"""app/middleware/rate_limit.py — 简单内存滑动窗口速率限制

历史背景：
  当前后端无任何速率限制 → 攻击者用脚本刷 /bridge/run 或 /worldbuild/start
  会触发昂贵 LLM 调用（每次可能 $0.01-$0.10）→ 钱包爆掉。

设计取舍（按部署章节"不在范围内"项最小化）：
  - 内存滑动窗口（不用 Redis）：单机部署够用，多 worker 时不严格公平
  - 仅限写端点（/bridge/run /worldbuild/start /bridge/review）：
    读端点（/health / /api/v1/projects）不限，避免误伤前端轮询
  - 默认 60 次/分钟/IP（每写端点独立计数）
  - 通过 env RATE_LIMIT_PER_MINUTE 调（生产建议调到 10-20）

注意：
  - 中间件用 dict 存每 IP 的窗口，时间复杂度 O(1) 摊销
  - 测试时设 RATE_LIMIT_PER_MINUTE=10000 避免误命中
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..logging_setup import get_logger

log = get_logger("novel_ai.rate_limit")


def _is_write_endpoint(path: str) -> bool:
    """判断 path 是否是写端点（POST/PUT/DELETE 的 expensive 路径）。

    中间件已经按 method 早退 GET/HEAD/OPTIONS，这里只看路径前缀。
    不需要逐端点列举 — 任何 /api/v1/ 下的非只读路径默认限速。

    已知写路径：
      - /api/v1/projects (POST 列表)
      - /api/v1/projects/{id}/bridge/* (run / review / push-concept 等)
      - /api/v1/projects/{id}/worldbuild/start
      - /api/v1/projects/{id}/chapters (POST)
      - /api/v1/projects/{id}/foreshadowings/{fid}/status (PUT)
      - /api/v1/projects/{id}/rules (PUT)
      - /api/v1/projects/{id}/ai-assist-level (PUT)
      - /api/v1/providers (POST/PUT/DELETE)
      - /api/v1/role-assignments/{key} (PUT)
    """
    # 明确豁免读路径（防止误伤 GET-only 路径）
    read_exact = ("/health", "/openapi.json")
    if path in read_exact:
        return False
    # 任何 /api/v1/ 下的非只读路径默认限速
    return path.startswith("/api/v1/")


class IPRateLimiter:
    """每个 IP 一个滑动窗口（deque of timestamps）。"""

    def __init__(self, max_per_minute: int = 60):
        self.max = max_per_minute
        self.window_seconds = 60
        # {ip: deque[float]}
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        """检查 IP 是否在当前窗口内还能访问。允许则记录时间戳。"""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets[ip]
            # 清掉过期时间戳
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        """测试用：清空所有 bucket。"""
        with self._lock:
            self._buckets.clear()


# 模块级单例（生产用，测试用 monkeypatch + reset 重置）
_limiter = IPRateLimiter(
    max_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
)


def configure(per_minute: int) -> None:
    """重新配置限流阈值（测试 / 启动时用）。"""
    global _limiter
    _limiter = IPRateLimiter(max_per_minute=per_minute)


def reset_for_testing() -> None:
    """测试 helper：清空所有 IP bucket。"""
    _limiter.reset()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware：每个写端点请求检查 IP 速率。

    Headers:
      - X-RateLimit-Limit: 总配额
      - X-RateLimit-Remaining: 当前窗口剩余
      - Retry-After: 被限流时距离下次可用的秒数
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # 只限制写端点 + 非 GET/HEAD/OPTIONS
        if method in ("GET", "HEAD", "OPTIONS") or not _is_write_endpoint(path):
            return await call_next(request)

        # 拿 IP（考虑 X-Forwarded-For，反代场景）
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )

        if not _limiter.is_allowed(ip):
            log.warning("rate limit hit: ip=%s path=%s method=%s", ip, path, method)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"每 {_limiter.window_seconds}s 最多 {_limiter.max} 次请求（写端点）",
                },
                headers={
                    "Retry-After": str(_limiter.window_seconds),
                    "X-RateLimit-Limit": str(_limiter.max),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        # 给响应加 rate limit headers（让前端能看到配额）
        response.headers["X-RateLimit-Limit"] = str(_limiter.max)
        # 剩余配额 = max - 当前窗口内计数（粗略估算，不严格）
        remaining = max(0, _limiter.max - len(_limiter._buckets.get(ip, [])))
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response