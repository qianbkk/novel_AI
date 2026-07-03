# CHANGELOG

本文档按时间倒序记录项目的所有重要变更。commit hash 是稳定锚点。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] — 2026-07-04

### Bug Fix（迭代 #34 — 内部审计）
- **`fix(engine): export_chapters 单章坏不能阻断整批导出**
  - `engine/tools/exporter.py` 之前单章坏让整个 export 失败：
    encoding 错 / meta 损坏 / OSError → 整批抛异常，**之前已写好的
    chapters 也没保存**。
  - 跟 import_chapters 是同型问题（迭代 #31），同样的修法。
  - 修法：每章独立 try/except，log warning + `continue` 跳过该章。
    同样修 `print_stats`（stats 视图同样需要单章坏不阻断）。
  - 加 3 个 invariant test 锁死：源码必须有 try/except + continue，
    正常文件场景跑通返回正确结果。

### Bug Fix（迭代 #33 — 内部审计）
- **`fix(api): SSE queue 内存泄漏**
  - `_run_queues` (bridge.py) 和 `_job_queues` (worldbuild/orchestrator.py)
    之前只创建 queue 从不清理。SSE consumer 读完 done 事件后 dict 里的
    queue 永远不被移除。
  - 后果：生产长期跑 N 个 run 后 dict 里堆 N 个 Queue + 内部 buffer，
    内存持续涨。重启后释放，但长跑进程会逐渐 OOM。
  - 修法：SSE consumer 退出（break / 异常 / 客户端断开）时通过
    `try/finally` 调 `cleanup_*_queue`，从 dict 移除 queue。
  - 加 5 个 invariant test 锁死：consumer 读 done 后 dict 被清理、
    重复清理幂等、event_generator 必须 try/finally 包裹。

### Bug Fix（迭代 #32 — 内部审计）
- **`fix(engine): MiniMax M3 reasoning_content 检测（避免静默空文本）**
  - `engine/llm/router.py:_minimax` 之前 line 456-458 对 reasoning_content
    存在但 content 为空的响应有死代码 fallback（重新赋 msg.get("content", "")
    还是空），导致 M3 思考模式被意外开启时静默返回空文本。
  - 后果：caller 拿到 "" 当成"正常生成" → 后续 checker 给空文本打 0 分
    PASS，save_and_track 落盘 0 字章节。
  - 触发场景：服务端配置变了 / 用户覆盖了 MINIMAX_BASE_URL 到旧版
    endpoint / 代理把 thinking 字段剥掉。
  - 修法：检测到 reasoning_content 非空 + content 空时显式 raise ValueError，
    让配置 bug 暴露而不是静默空文本污染下游。
  - 加 3 个 invariant test 锁死：reasoning_content + empty content → raise；
    正常 content → 正常返回；content 空 + 无 reasoning_content → 走兜底
    text 字段。

### Bug Fix（迭代 #31 — 内部审计）
- **`fix(bridge): import_chapters 单文件坏不能阻断整批**
  - `app/bridge/chapter_import.py` 之前一个坏文件就让整批 import 失败：
    - 文件名畸形（ch_xyz.txt 而不是 ch_0001.txt）→ IndexError
    - 编码错（Latin-1 而非 UTF-8）→ UnicodeDecodeError
    - meta.json 损坏 → JSONDecodeError
  - 后果：50 章里只要有 1 章坏 → import 抛异常 → 0 章导入，
    用户没法定位是哪个文件坏。
  - 修法：每文件独立 try/except，log warning + 跳过该文件继续下一个；
    同样修 `_force_reimport`。
  - 加 2 个 invariant test 锁死：3 个文件（1 正常 + 1 meta 坏 + 1 坏 filename）→
    正常文件被导入，整个 import 不抛异常。

### Bug Fix（迭代 #30 — 内部审计）
- **`fix(api): run_bridge 删除死锁代码（false sense of security）**
  - `app/api/bridge.py` 之前用 `_get_project_lock(project_id).locked()` 做
    "同 project 重复 run"并发保护，但该 `asyncio.Lock` 永不被 acquire
    （grep 证实无 `async with _get_project_lock`），检查永远 False
    → 给 false sense of security（代码看起来"有锁"但实际没有）。
  - 真实保护只有两层：
    1) DB 层 `BridgeRun.status='running'` 检查
    2) lifespan 启动时 `_recover_orphan_bridge_runs`（清理崩溃遗留）
  - 修法：删 `_project_locks` 字典 + `_get_project_lock()` 函数 + 调用点，
    注释说明 DB 层 + orphan recovery 是真实保护。
  - 副作用：tests/test_phase1_5_smoke.py 也 import 了已删的 `_get_project_lock`
    导致 collection error，顺手修：删 import + 删 asyncio.Lock 单测段（保留
    SQL 409 兜底测试）。
  - 加 2 个 invariant test 锁死：bridge.py 不应再定义/调用 _project_locks；
    run_bridge 真代码行不该有 .locked() 假并发检查。

### Bug Fix（迭代 #29 — 内部审计）
- **`fix(bridge): apply_review 静默 pop 错任务**
  - `app/bridge/reports.py:152-169` 之前 `_find_task_index` 在没匹配时
    fallback 到 0 — 用户提交 review with task_id="X" 但 X 不存在时，
    第一条 pending 被静默移除（数据完整性破坏）。
  - 后果：review_history 记的是 "X" 但实际 pop 的是另一条 task；
    用户以为"处理了 X"但 pending 列表里 task-A（不是 X）消失了。
  - 修法：_find_task_index 在没找到时显式返回 None（不 fallback）；
    apply_review 加 `matched` 字段告诉前端"是否匹配"，方便 UI 显示"未匹配"。
  - 加 3 个 invariant test 锁死：unmatched task_id/chapter_number 不 pop，
    matched task_id pop 对的任务。

### Bug Fix（迭代 #28 — 内部审计）
- **`fix(engine): node_rewrite post-rewrite compliance fake-pass**
  - `orchestrator.py:391-394` 之前当 `run_compliance`（post-rewrite）抛异常
    时兜底 `comp_result = {"passed": True}`，跟之前修过的 `node_write_pipeline`
    里的 compliance fake-pass 同型问题。
  - 后果：重写后即便合规检查完全失败（异常被吞），章节也走"通过"路径
    → 违规内容落盘 + checker 用 stale cr 可能误判 save。
  - 修法：跟 node_write_pipeline 对称 — 标记 `_compliance_check_failed=True`
    并提前 return；同时给 `route_after_rewrite` 加防御性检查（防止旧 cr
    分数遮蔽新失败标记）。
  - 加 4 个 invariant test 锁死：post-rewrite compliance 抛异常 → escalate。

- **`fix(engine): node_load_arc_tasks outline cost 双重计费**
  - `orchestrator.py:209` 之前在 try/except 之外多调一次 `_add_cost(state, cost)`，
    而每个分支（card / talk / batch）内部已经调过 → 实际计费 2 倍。
  - 后果：50 章跑下来 `budget_used_usd` 虚高 100%，超预算提前 escalate。
  - 修法：删掉 line 209 的重复调用，保留分支内部调用。
  - 加 4 个 invariant test 锁死：batch/card/talk 三种模式各只增一次，
    异常时不应计费。

### Bug Fix（独立 AI 审查发现）
- **`aa969a5` fix(engine): orchestrator human_escalation 边 → load_arc_tasks**
  - 独立 AI 深度审查（2026-07-03）发现：`orchestrator.py:573` 之前是
    `human_escalation → END`，与 `engine/graph.py:290` 不一致。
  - 后果：run/resume 章节触发人工介入 → stream() 立即终止 →
    chapters_done < max_chapters 但 exit_code=0（静默提前结束）。
  - 修法：把 orchestrator 边改成 load_arc_tasks，加 3 个 invariant test
    锁死两个文件的图拓扑必须一致。

### Tests（持续加固）
- 本轮新增 invariant test 类：TestMockProviderEndToEnd /
  TestOpenApiExport / TestMasterKeyRotation / TestOpenApiExportEndToEnd /
  TestMasterKeyScriptsEndToEnd / TestRotateMasterKeyEndToEnd /
  TestGraphCommandFailurePaths / TestSaveStateTrueConcurrency /
  TestBudgetManager / TestAuditProjectItself / TestMigrationsIdempotent /
  TestGetDbDependency / TestApplyReviewInputValidation /
  TestLoadStateRobustness / TestDocCodeConsistency /
  TestSecurityConstants / TestProviderTableSchema /
  TestHumanEscalationNotEndRun / 等
- 总 invariant suite：**216 passed / 0 warnings**

## [Unreleased] — 2026-07-02

### Security（高危）
- **`889a47e` fix(security): Provider API key 加密存储（Fernet + MASTER_KEY env）**
  - 历史背景：`Provider.api_key` 之前明文存 SQLite，DB 泄漏 = 全部供应商 key 曝光
  - 新增 `backend/app/security.py`（Fernet encrypt/decrypt + MASTER_KEY bootstrap）
  - 新增 `backend/app/migrations.py`（启动时 idempotent ALTER TABLE）
  - Schema：`api_key` 列已 DROP，新增 `api_key_encrypted`（ciphertext）+ `api_key_suffix`（明文后 4 位）
  - `ProviderOut` 不再返回明文，只返回 `api_key_set` + `api_key_suffix`
  - 前端 `Providers.tsx`：编辑时必须重新填 api_key（后端不返回明文，无法预填）
  - 部署前必设 `MASTER_KEY` env；脚本：`python -m scripts.generate_master_key`

- **`c8f764b` fix(main): lifespan handler + BridgeRun 孤儿自愈 + CORS 收紧**
  - 启动时清理孤儿 `BridgeRun.status="running" & finished_at IS NULL` 行（进程崩溃后无法再 409）
  - CORS 从 `*` 收紧为默认 `[http://localhost:5293]`，可通过 `ALLOWED_ORIGINS` env 覆盖
  - 弃用 `@app.on_event` → `@asynccontextmanager lifespan`

### Bug Fixes
- **`af3ddc4` fix(engine): llm_router 读 api_key_encrypted** — 之前读已删字段
- **`4f79ae4` fix(bridge): reports.py 路径统一** — 走 `NOVEL_AI_DIR` env，与 engine 一致
- **`2055746` fix(bridge): 清理 _run_bridge_async 死代码**
- **`e7b7215` fix(api): submitReview 字段对齐** — 前端 `edited_content` → `content`
- **`d503446` fix(ports): 统一 backend 端口 8123→8132**（README/dev.bat/docs/run_mvp 一并改）

### Chore（依赖升级 / 弃用清理）
- **`d618dd4` chore(deps): Pydantic class Config 迁 ConfigDict + datetime.utcnow() 弃用清理**
  - 7 处 `class Config: from_attributes=True` → `model_config = ConfigDict(...)`
  - 9 处 `datetime.utcnow()` → `datetime.now(timezone.utc)`
  - pytest warnings 从 15 降到 0

### Features
- **`bfd68cd` feat(engine): Mock LLM provider**
  - 不读任何 API key env，CI 不需要 secret
  - 每个 agent 给 schema 化 JSON 固定响应
  - writer 模拟 ~2000 字章节满足 `call_with_length_budget`

### Refactor
- **`9418791` refactor(engine): graph.py 日志统一** — 16 处 `capture.write("[engine] ...")` → `log.xxx(...)`

### Docs
- `README.md` 加「部署」章节（MASTER_KEY / CORS / 端口 / 迁移 / 范围外）
- `docs/superpowers/plans/2026-06-27-phase1-5-fusion-debugs.md` 标记 SUPERSEDED + commit 索引

### Tests（invariant suite）
`pytest tests/` 从 22 → **96 passed**，0 warnings。新增关键测试类：
- `TestFrontendBackendPortConsistency`（5）— 端口硬编码锁死
- `TestReviewContract`（3）— submitReview schema 一致
- `TestBridgeDeadCodeRemoved`（2）— `_run_bridge_async_imported` 不再出现
- `TestOrphanBridgeRunRecovery`（5）— lifespan cleanup 真测
- `TestReportsPathUnified`（3）— `NOVEL_AI_DIR` env 解析
- `TestProviderApiKeyEncrypted`（7）— 明文不入库 + API 不暴露
- `TestMockLLMProvider`（4）— mock provider 离线可用
- `TestEngineLoggingUnified`（2）— `[engine] capture.write` 已清零
- `TestFrontendTypesAligned`（2）— BridgeRun + ChapterFull 类型
- `TestDeploymentDocs`（2）— MASTER_KEY 脚本 + README 部署章节
- 还有 `TestParseLLMJsonResponseTypeGuard`（7）、`TestTrackerUsesParseWithDictDefault`（3）、`TestSaveStateUpdatesLastUpdated`（2）等

---

## 2026-07-01 — Phase 1.5 收尾 + 12 commit 修复链

| Commit | 类型 | 标题 |
|---|---|---|
| `62baf44` | bug | run 进程走 subprocess（uvicorn 重启不杀 in-flight run） |
| `dd1e14a` | bug | writer / rewriter 网络异常重试一次 |
| `e4eaca1` | bug | orchestrator 全 pipeline 异常走 escalate（5 处 fake-pass） |
| `08a8f02` | bug | state 路径统一 NOVEL_AI_DIR env |
| `5d1f83e` | bug | writer 失败不再写 `[writer-stub]` 假 PASS |
| `17a20fc` | fix | parse None 注释 / monitor 文档 / 测试不依赖活服务 |
| `936f58d` | chore | 删 FUSION_BUILD_SPEC.md 死文档 |
| `33a5c09` | bug | save_state 自动更新 last_updated |
| `48870c6` | feat | monitor_run.py 后台监控脚本 |
| `af8f073` | bug | parse_llm_json_response 类型保护（tracker bug 根因） |
| `3278a77` | bug | 前端端口 8123→8132 |
| `bdff57a` | bug | graph.stream 必须传 thread_id（17 小时静默失败的根因） |

---

## 2026-06-28 — Phase 1 融合

| Commit | 标题 |
|---|---|
| `a481006` | feat: schema-driven contracts + audit + invariant tests (5 root-cause fixes) |
| `efd6345` | fix: chapter-entity backfill + junk-header strip + v3 guide (#2) |
| `8955017` | Merge pull request #1 |
| `82865ea` | fix(bridge): worldbuild data + chapter titles + persistent logs |
| `9ad873e` | fix(engine): unblock 50-chapter run + planner/init_arc shortcuts |
| `cb73b3c` | fix(frontend): Dashboard / WorldBuild 错误态加显式提示 |
| `58b9a3a` | chore: 删 docs/ 旧版 html |
| `4a3cef3` | feat(frontend): 设计系统升级 — 高级优雅 · 工业曲线 · 微交互 |
| `d93a3d0` | feat: 补齐 4 处 review 缺口 |
| `dea9f59` | feat(api+ui): 补齐前后端缺失接口 |
| `0ca95a0` | feat(engine): P2/P3 完成 — 8 agents 真实实现 + L2/L5 记忆 + SqliteSaver + 10 tools |
| `e24223b` | feat(engine): drop novel_AI/ dependency — backend now runs independently |

---

## 历史里程碑

- **Phase 1（2026-06-26 ~ 06-28）**：novel-assistant + novel_AI 融合，backend 从依赖 novel_AI/ 切到独立运行
- **Phase 1.5（2026-06-29 ~ 07-01）**：12 commit 收尾链，1 个 commit 修了 17 小时静默失败的根因（thread_id 缺失）
- **深度修复轮（2026-07-02）**：10 commit 全面修复 — API key 加密、孤儿 running 自愈、Mock provider、Pydantic/utcnow 弃用清理、logging 统一、文档补全