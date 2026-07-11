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

反代部署的安全：
  - X-Forwarded-For 默认不可信（攻击者可伪造任意 IP 绕过限流）
  - 通过 env ALLOWED_PROXIES 配置反代 IP 白名单（逗号分隔 CIDR/IP）
  - 仅当直接连接 IP 在白名单内才用 X-Forwarded-For 第一段
  - 直接暴露 uvicorn 时不要配 ALLOWED_PROXIES → 自动用 request.client.host

个人使用豁免（2026-07）：
  单租户本地原型阶段，limit 127.0.0.1 / ::1 默认无限流 —— 在 localhost 上
  自己刷自己的接口只是摩擦。生产真暴露公网时设 RATE_LIMIT_EXEMPT_LOCALHOST=0
  关掉此豁免即可。

注意：
  - 中间件用 dict 存每 IP 的窗口，时间复杂度 O(1) 摊销
  - 测试时设 RATE_LIMIT_PER_MINUTE=10000 避免误命中
"""
from __future__ import annotations

import ipaddress
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


def _parse_allowed_proxies() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """从 env 解析反代 IP 白名单（逗号分隔）。

    支持：
      - 单个 IP：127.0.0.1
      - CIDR：10.0.0.0/8
    未配 / 解析失败 → 返回空列表（所有反代 IP 都不信任 → fallback 到 request.client.host）
    """
    env = os.environ.get("ALLOWED_PROXIES", "").strip()
    if not env:
        return []
    out = []
    for token in env.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            # ip_network 接受 "127.0.0.1" 和 "127.0.0.0/24"
            out.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            log.warning("ALLOWED_PROXIES 含无效 IP/CIDR：%r（跳过）", token)
    return out


def _ip_in_allowed_list(ip_str: str, allowed: list) -> bool:
    """检查 ip_str 是否在任一 allowed network 内。"""
    if not allowed:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in allowed)


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
        # 迭代 #81: counter for opportunistic stale-bucket cleanup sweep
        self._request_count: int = 0

    def is_allowed(self, ip: str) -> bool:
        """检查 IP 是否在当前窗口内还能访问。允许则记录时间戳。

        迭代 #81: 之前 _buckets dict 长期跑会积累 stale IP 条目（每个 IP
        至少有一个 deque 条目占用内存）。审计报告说"单租户原型场景下可以
        忽略"，但加个简单的"每 N 次请求扫一遍清 stale"就能解决。

        修法：counter-based opportunistic sweep——每 1000 次请求就扫一次
        _buckets，把所有时间戳全部过期的 IP 从 dict 中删除。lock 内 O(N)
        但 1000 次请求才触发一次，摊销成本可忽略。
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets[ip]
            # 清掉过期时间戳
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max:
                # 迭代 #81: 顺便 opportunistic sweep——每 1000 次请求清一次 stale
                # (避免每次 lock 内 O(N)，分摊到 1000 次里)
                self._request_count += 1
                if self._request_count % 1000 == 0:
                    self._cleanup_stale_locked(cutoff)
                return False
            bucket.append(now)
            self._request_count += 1
            if self._request_count % 1000 == 0:
                self._cleanup_stale_locked(cutoff)
            return True

    def _cleanup_stale_locked(self, cutoff: float) -> int:
        """内层 helper：必须在 self._lock 内调用。清掉所有时间戳都过期的 bucket。

        Returns:
            清理数量。
        """
        stale = [ip for ip, b in self._buckets.items()
                 if not b or b[-1] < cutoff]
        for ip in stale:
            del self._buckets[ip]
        return len(stale)

    def cleanup_stale_buckets(self) -> int:
        """显式清理 stale buckets（运维可选 / 长跑定期调用）。

        通常不需要——is_allowed 已经每 1000 次请求做一次 opportunistic sweep。
        但启动恢复 / 大量 IP 同时活跃时可显式调一次。

        Returns:
            清理的 bucket 数（用于测试可观测性）。
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            return self._cleanup_stale_locked(cutoff)

    def reset(self) -> None:
        """测试用：清空所有 bucket。"""
        with self._lock:
            self._buckets.clear()


# 模块级单例（生产用，测试用 monkeypatch + reset 重置）
_limiter = IPRateLimiter(
    max_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
)

# 本地回环豁免：默认开（个人使用），生产设 RATE_LIMIT_EXEMPT_LOCALHOST=0 关掉。
_LOCALHOST_IPS = frozenset({"127.0.0.1", "::1"})


def _localhost_exempt() -> bool:
    """个人使用豁免开关：默认 True（不在限流清单里加 localhost）。"""
    return os.environ.get("RATE_LIMIT_EXEMPT_LOCALHOST", "1").strip() not in ("0", "false", "False")


def configure(per_minute: int) -> None:
    """重新配置限流阈值（测试 / 启动时用）。"""
    global _limiter
    _limiter = IPRateLimiter(max_per_minute=per_minute)


def reset_for_testing() -> None:
    """测试 helper：清空所有 IP bucket。"""
    _limiter.reset()


# ════════════════════════════════════════════════════════════════════════
# Phase B1：登录端点单独限流（per-IP+email，15 分钟内最多 5 次失败）
# ════════════════════════════════════════════════════════════════════════
#
# 为什么全局 IPRateLimiter 不够：
#   - 全局中间件只按 IP 限流；如果一个攻击者用同一 IP 集中攻击多个 email，
#     会因为"非写端点"豁免（_is_write_endpoint 判定 /auth/login 不在
#     /api/v1/ 路径下，实际不走全局限流）。
#   - 需要 per-(IP, email) 维度的失败计数，登录成功时重置（让真实用户
#     在偶尔输错后不被永久锁）。
# 设计取舍：
#   - 不做"账号锁定"持久化字段（避免引入 User.account_locked 状态和
#     运营复杂度；只是速率限制，攻击者等 15 分钟就能继续尝试）。
#   - 复用现有 IPRateLimiter 的 deque + lock 模式，不引入新的存储。
#   - key 用 (ip, email) 字符串拼接作 dict key（小写 email 防大小写绕过）。

class LoginRateLimiter:
    """Per-(IP, email) 失败计数：15 分钟窗口内最多 5 次失败 → 429。

    成功调用 reset() 后清零（让真实用户偶尔输错后立即能用）。
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 15 * 60):
        self.max = max_failures
        self.window = window_seconds
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _key(ip: str, email: str) -> str:
        # email 必须先 normalize（小写 + strip）才能让登录路径调用前做好，
        # 这里再做一次兜底
        return f"{ip}::{email.strip().lower()}"

    def is_allowed(self, ip: str, email: str) -> bool:
        """检查是否还能尝试登录（窗口内失败 < max）。

        Returns:
            True — 允许尝试（无论成功失败本函数都不增计数，由调用方按结果
                  调 record_success 或 record_failure）
            False — 已被限流（窗口内失败 ≥ max）

        设计：is_allowed 只检查不增计数，是为了把"增计数"显式分到成功/
        失败两个分支——这样 record_success 能 reset 计数，让真实用户不
        会因为偶尔输错密码被永久锁。
        """
        now = time.monotonic()
        cutoff = now - self.window
        key = self._key(ip, email)
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return len(bucket) < self.max

    def record_failure(self, ip: str, email: str) -> None:
        """记一次失败。"""
        now = time.monotonic()
        key = self._key(ip, email)
        with self._lock:
            self._buckets[key].append(now)

    def record_success(self, ip: str, email: str) -> None:
        """登录成功 → 清零计数器（让真实用户偶尔输错后立即可用）。"""
        key = self._key(ip, email)
        with self._lock:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        """测试 helper：清空所有 (ip, email) bucket。"""
        with self._lock:
            self._buckets.clear()


_login_limiter = LoginRateLimiter()


def get_login_limiter() -> LoginRateLimiter:
    """暴露单例给 endpoint 调用 + 测试 reset。"""
    return _login_limiter


def reset_login_limiter_for_testing() -> None:
    """测试 helper：清空 login limiter。"""
    _login_limiter.reset()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware：每个写端点请求检查 IP 速率。

    Headers:
      - X-RateLimit-Limit: 总配额
      - X-RateLimit-Remaining: 当前窗口剩余
      - Retry-After: 被限流时距离下次可用的秒数

    反代部署必须设 ALLOWED_PROXIES（逗号分隔 IP/CIDR），否则 X-Forwarded-For
    不可信，攻击者能伪造 IP 绕过限流。
    """

    # 模块级缓存反代白名单（避免每个请求都重新解析 env）
    _allowed_proxies: list | None = None

    @classmethod
    def _get_allowed_proxies(cls) -> list:
        if cls._allowed_proxies is None:
            cls._allowed_proxies = _parse_allowed_proxies()
        return cls._allowed_proxies

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # 只限制写端点 + 非 GET/HEAD/OPTIONS
        if method in ("GET", "HEAD", "OPTIONS") or not _is_write_endpoint(path):
            return await call_next(request)

        # 拿真实客户端 IP：
        # - 直接连接 uvicorn：request.client.host 唯一可信源
        # - 反代：request.client.host 是反代 IP，必须在 ALLOWED_PROXIES 才信任 XFF
        direct_ip = request.client.host if request.client else "unknown"

        # 个人使用豁免：本地直连（含 IPv6 ::1）默认不限流（单租户原型阶段
        # 自己在 localhost 上刷接口是纯摩擦）。生产暴露公网时设
        # RATE_LIMIT_EXEMPT_LOCALHOST=0 关掉。
        if _localhost_exempt() and direct_ip in _LOCALHOST_IPS:
            return await call_next(request)

        allowed = self._get_allowed_proxies()
        if allowed and _ip_in_allowed_list(direct_ip, allowed):
            # 反代在白名单内 → 信任 XFF 第一段
            xff = request.headers.get("x-forwarded-for", "")
            ip = xff.split(",")[0].strip() or direct_ip
        else:
            # 直接连接 或 反代不在白名单 → 用 direct IP（不信任 XFF）
            ip = direct_ip

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