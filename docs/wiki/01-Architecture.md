# 架构总览

## 三层拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (React + TS + Vite, :5293)                                  │
│  控制台：项目管理 / Provider 配置 / 角色分配 / 世界构建向导 / 写作引擎控制台 │
└───────────────────────────────┬───────────────────────────────────────┘
                                 │ fetch (JSON) + EventSource (SSE)
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Backend (FastAPI, :8132)                                              │
│  ┌───────────────┐  ┌────────────────┐  ┌────────────────────────┐   │
│  │ app/api/*      │  │ app/worldbuild  │  │ app/rag                │   │
│  │ 12 个路由模块   │  │ 10 阶段世界构建  │  │ embedding + 语义检索    │   │
│  └───────────────┘  └────────────────┘  └────────────────────────┘   │
│  ┌───────────────┐  ┌────────────────┐  ┌────────────────────────┐   │
│  │ app/bridge/    │  │ app/auth*      │  │ app/security           │   │
│  │ 引擎桥接四模块  │  │ 单/多租户鉴权   │  │ Fernet 加密 Provider key│   │
│  └───────┬───────┘  └────────────────┘  └────────────────────────┘   │
│          │ subprocess.Popen(独立 OS 进程)                              │
└──────────┼──────────────────────────────────────────────────────────┘
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Engine (backend/engine, 以子进程运行 engine/workers/run_bridge_subprocess.py) │
│  LangGraph 6 节点状态机 → 调度 9 个 Agent（Planner/Outline/Writer/Normalizer/ │
│  Compliance/Checker×3/Rewriter/Tracker/Summarizer）                    │
│  读写 <novel_ai_dir>/{config,output,logs}/*.json,*.txt                │
└─────────────────────────────────────────────────────────────────────┘
```

## 关键设计决策

### 1. 引擎作为独立子进程，而非进程内调用

`POST /projects/{id}/bridge/run` 不会在 FastAPI 的 event loop 里直接跑 LangGraph 图，而是 `subprocess.Popen` 拉起一个全新的 `python engine/workers/run_bridge_subprocess.py` 进程（[app/api/bridge.py](../../backend/app/api/bridge.py) `_spawn_engine_subprocess`）。

**原因**：写作一章可能耗时数十秒到数分钟（多次 LLM 调用 + 重写循环），而开发时 `uvicorn --reload` 一旦检测到代码变更就会重启，若引擎跑在同一进程里会被杀死。子进程方案让写作任务独立于 Web 服务的生命周期。

后端通过一个后台线程逐行读取子进程 stdout，写入 `BridgeRun.stdout_text` 并推入内存 `Queue`，前端通过 `GET /bridge/stream`（SSE）消费该队列。

### 2. 文件系统作为引擎与后端的数据交换介质

引擎不直接读写后端数据库；它只认识 `<novel_ai_dir>/` 下的固定文件布局（`config/novel_config.json`、`output/setting_package.json`、`output/chapters/ch_N.txt`+`_meta.json`、`output/orchestrator_state.json`、`logs/budget_log.jsonl`）。后端与引擎之间靠 **push / pull** 两侧显式同步：

- `POST /bridge/push-concept` — 后端项目配置 → 写入引擎的 `novel_config.json`
- `POST /bridge/pull-setting` → 读取引擎生成的 `setting_package.json`，按 JSON Schema 校验后拆解写入 `WorldSetting`/`Character`/`Faction` 等多张表
- `POST /bridge/import-chapters` → 读取引擎写出的章节 txt，做 embedding、角色打标、入库

`NovelAIBinding` 表记录每个项目绑定的 `novel_ai_dir` 路径（可指向仓库外的独立 `novel_AI/` 目录，也可指向 `backend/data/engine/`）。

### 3. Provider / 角色路由与引擎自身路由是两套系统

- `app/llm_router.py` + `app/llm_client.py`：**世界构建向导**自己的 LLM 调用（3 个角色：structured_logic / creative_detail / consistency_check），走后端自己的 provider 解析逻辑。
- `engine/llm/router.py`（`LLMRouter`）：**写作引擎**（9 个 Agent）用的路由，由 `engine/llm_router.py` 从数据库读取 `Provider`/`RoleAssignment`（15 个角色）注入。

两者都最终读同一张 `providers` 表和 `role_assignments` 表（15 个角色是并集，向导只用其中 3 个），但代码路径独立，互不调用。

### 4. 单租户开发模式 vs 生产多租户模式

默认（`NOVEL_PRODUCTION` 未设置）是**单租户 dev 模式**：不登录也能操作所有项目，`owner_id IS NULL` 的数据全局可见。设置 `NOVEL_PRODUCTION=1` 后：

- 启动时 fail-fast 检查 `MASTER_KEY`、`JWT_SECRET`、`ALLOWED_ORIGINS` 等生产配置（[app/main.py](../../backend/app/main.py) `_check_production_hardening`）
- 所有 project-scoped 接口强制要求登录，`owner_id IS NULL` 的行返回 403（不再全局可见）

详见 [02-Backend-API.md](02-Backend-API.md#认证与多租户模型)。

## 请求生命周期示例：跑一章写作

1. 用户在前端「写作引擎控制台」页点击「写 N 章」→ `api.triggerBridge(projectId, "run", ["N"])` → `POST /projects/{id}/bridge/run`
2. 后端校验：世界构建已完成、无并发运行中的 `BridgeRun`、命令属于 `WRITE_COMMANDS` → 创建 `BridgeRun(status="pending")` 行 → `background_tasks.add_task(_spawn_engine_subprocess, ...)`
3. 子进程启动，环境变量注入 `NOVEL_AI_DIR`、`NOVEL_AUDIT_MODE`（项目级）、`NOVEL_OUTLINE_MODE`
4. `run_bridge_subprocess.py` 调用 `engine.graph.run_graph_task(...)`，LangGraph 状态机开始跑：`load_arc_tasks → get_next_task → write_pipeline →(评分不过)→ rewrite →(仍不过 3 次)→ human_escalation`，或 `→(评分通过)→ save_and_track → get_next_task`（循环直到章节数写完）
5. 引擎每一步的日志通过 `print()` 输出到 stdout，被后端后台线程捕获，转发为 SSE `log` 事件
6. 前端 `BridgeConsole.tsx` 用 `EventSource` 监听，实时渲染日志流、当前节点、预算消耗
7. 章节写完后，用户点击「导入章节」→ `POST /bridge/import-chapters` → 章节文本入库、embedding、语义检索可用

## 环境边界与外部依赖

- **LLM Provider**：Anthropic Claude、DeepSeek、Gemini、Kimi、MiniMax 或自定义 OpenAI 兼容端点，经 `Provider` 表配置，API Key 用 Fernet 加密存储于 SQLite。
- **Embedding**：默认走 DashScope（阿里云）`text-embedding-v3`（Qwen3-Embedding），无 key 时自动降级为确定性 mock 向量（离线开发友好）。
- **数据库**：SQLite（`novel_assistant.db` + LangGraph 自身的 `checkpoints.sqlite`），启动时自动在线备份快照。
- **无外部消息队列/缓存**：并发控制、SSE 队列、限流窗口均为进程内内存结构（重启即丢失，符合单机原型定位）。
