# CHANGELOG

本文档按时间倒序记录项目的所有重要变更。commit hash 是稳定锚点。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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