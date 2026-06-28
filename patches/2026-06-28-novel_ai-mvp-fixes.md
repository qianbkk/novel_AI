
# novel_AI MVP 修复 — 2026-06-28
# 适用环境：本地 Windows MVP，跑得动小说优先
# 原文件：novel_AI/api_client.py + novel_AI/orchestrator.py
# 这些修复没被 git 跟踪（novel_AI/ 在 .gitignore），需要手动 apply 到别处的机器。

# ==== Fix 1: api_client.py — 5 处 with _get_client(X) as c: → 裸调用 ====
# 原:
#     with _get_client(120) as c:
#         r = c.post(URL, headers=headers, json=payload)
#         r.raise_for_status()
#         data = r.json()
# 改:
#     c = _get_client(120)
#     r = c.post(URL, headers=headers, json=payload)
#     r.raise_for_status()
#     data = r.json()
# 涉及 line 174/200/224/268/329（5 处）
# 原因：with 块退出时 httpx.Client.__exit__ 关闭连接池，连接池失效，每次请求都新建连接

# ==== Fix 2: orchestrator.py — node_rewrite 补 run_compliance ====
# 在 line 222-223 (run_normalizer 之后) 和 line 225 (run_checker 之前) 之间插入:
# 
#     # Bug 2 fix: re-verify compliance after rewrite
#     comp_result, cost = run_compliance(clean_text, state.get("platform", "fanqie"))
#     _add_cost(state, cost)
#     if not comp_result["passed"]:
#         log(f"  🛡️  重写后仍违规：{comp_result.get('reason', '')}", state)
#         task["_draft_text"]          = clean_text
#         task["_compliance_failed"]   = True
#         task["_compliance_feedback"] = comp_result.get("reason", "违规内容需重写")
#         state["current_task"]        = task
#         return state  # skip checker; route_after_rewrite 路由回 rewrite
#
# 原因：原版 node_rewrite 只跑 rewriter→normalizer→checker，绕开 compliance，
#       重写后的违规内容会被 _compliance_failed=False 放行

# ==== Fix 3: orchestrator.py — 放宽预算 ====
# 原 line 28-29:
#     BUDGET_WARN   = 0.80   # 80%发警告
#     BUDGET_HARD   = 0.95   # 95%硬停
# 改:
#     BUDGET_WARN   = 1.00   # 100%发警告（MVP 放宽）
#     BUDGET_HARD   = 1.50   # 150%硬停（防失控，95% 太严）
# 原因：MVP 阶段用户预算限制放轻，先跑通完整长篇再回头看账单

# ==== Fix 4: api_client.py — tenacity 网络重试 ====
# 顶部 import 加一行:
#     from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
# 在 _get_client 之后加 helper:
#     class _HTTPClientError(httpx.HTTPError):
#         def __init__(self, status_code, message):
#             super().__init__(message)
#             self.status_code = status_code
#
#     @retry(
#         stop=stop_after_attempt(3),
#         wait=wait_exponential(min=1, max=10),
#         retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
#         reraise=True,
#     )
#     def _post_with_retry(client, url, **kwargs):
#         r = client.post(url, **kwargs)
#         if 500 <= r.status_code < 600:
#             r.raise_for_status()  # HTTPStatusError → 被 retry 捕获
#         elif 400 <= r.status_code < 500:
#             raise _HTTPClientError(r.status_code, f"HTTP {r.status_code}: {r.text[:200]}")
#         return r
# 把 5 处 'r = c.post(URL, ...); r.raise_for_status()' 替换为 'r = _post_with_retry(c, URL, ...)'：
#   _deepseek (line 201)
#   _gemini  (line 226)
#   _kimi    (line 249)
#   _minimax (line 292)
#   _custom  (line 352)
# 原因：长跑 1500 章时偶发 5xx / 网络抖动会单点失败；3 次指数退避（1s/2s/4s 起步）
#       覆盖大多数暂时性故障，不重试 4xx（认证/配额错误，重试无意义）
# 依赖：tenacity（backend/ requirements.txt 没加，因为 backend/ 不用；
#       只 novel_AI/ 用；novel_AI 本身没 requirements.txt，假设用户环境已有 tenacity=9.x）
