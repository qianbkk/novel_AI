
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
