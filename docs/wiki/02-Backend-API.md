# Backend（`backend/app/`）

FastAPI 应用，端口 `8132`。入口 `backend/app/main.py`。

## 启动流程（`main.py`）

导入期：`configure_root()` 配置日志（控制台 + `backend/logs/novel_ai.log` 滚动文件）→ `Base.metadata.create_all()` 建表 → `run_migrations()`（`app/migrations.py`，幂等 `ALTER TABLE ADD COLUMN`，处理 SQLite `create_all` 无法覆盖的 schema 变更）。

`lifespan`（`asynccontextmanager`，main.py:154-185）启动阶段依次执行：

1. `_check_master_key_in_production()`（main.py:26）— `NOVEL_PRODUCTION=1` 且未设 `MASTER_KEY` 时 fail-fast，防止加密的 Provider Key 重启后永久无法解密。
2. `_check_production_hardening()`（main.py:59）— 生产模式下校验 `ALLOWED_ORIGINS`、`RATE_LIMIT_EXEMPT_LOCALHOST`、`JWT_SECRET` 是否已妥善配置。
3. `run_migrations()`。
4. `seed_role_assignments(db)` — 为 15 个写作角色播种空的 `RoleAssignment` 行。
5. `_recover_orphan_bridge_runs()`（main.py:119）— 将上次崩溃遗留的 `status='running'` 且无 `finished_at` 的 `BridgeRun` 标记为 `failed`。
6. `take_all_snapshots()`（`app/backup_db.py`）— SQLite 在线备份。

**中间件**：CORS（`get_allowed_origins_list()`，默认 `http://localhost:5293`）→ `RateLimitMiddleware`（仅写操作生效的滑动窗口限流）。

**路由注册**（main.py:204-215）：`auth`、`projects`、`worldbuild.router` + `worldbuild.meta_router`、`chapters`、`providers`、`role_assignments`、`bridge`、`rules`、`foreshadowings`、`ai_assist`、`world`。

`GET /health` 实际探测数据库（`SELECT 1`），失败返回 503 JSON，而非静态 "ok"。

## 认证与多租户模型

单租户 dev 模式为默认；`NOVEL_PRODUCTION=1` 切换为强制鉴权的生产模式。

- `get_current_user_optional`（`app/auth.py:240`）永不抛 401：Bearer header 或 `novel_ai_token` HttpOnly cookie 缺失/无效时返回 `None`。
- 每个 project-scoped 路由模块自定义 `_owner_check(request, project_id, db)`，调用 `require_owned_project`（`app/auth_scope.py:52`）：
  - dev 模式下 `current_user is None` → 放行任意项目（向后兼容）
  - 生产模式下必须登录；`owner_id IS NULL` 的行返回 403（不再全局可见）
- `owner_filter_clause`（`auth_scope.py:36`）为列表接口构造 `WHERE owner_id = user.id OR owner_id IS NULL`
- 第一个注册的用户会自动接管所有历史 `owner_id=NULL` 的项目

### `/auth` 路由（`app/api/auth.py`）

| 方法 路径 | 说明 |
|---|---|
| `POST /auth/register` | 创建用户，回填历史 `owner_id=NULL` 项目，签发 JWT（body + HttpOnly cookie） |
| `POST /auth/login` | bcrypt 校验，按 (IP, email) 限流（15 分钟 5 次失败），签发 JWT |
| `GET /auth/me` | 当前用户信息 |
| `POST /auth/change-password` | 改密码 |
| `POST /auth/dev/reset-jwt-secret`, `GET /auth/dev/_users` | 仅 dev 可用，生产模式下 404 |

### 安全机制

- **Provider API Key 加密**（`app/security.py`）：Fernet 对称加密。`MASTER_KEY` 解析顺序：环境变量 → `backend/data/.dev_master_key` 持久化文件（跨 `--reload` 存活）→ 临时生成 + 警告。生产模式下缺失 `MASTER_KEY` 直接拒绝启动。API 响应只暴露末 4 位（`api_key_suffix`），从不返回明文/密文。
- **JWT**（`app/auth.py`）：HS256，7 天有效期，密钥解析策略与 `MASTER_KEY` 相同。登录/注册同时下发 JSON body 中的 `access_token` 和 HttpOnly + `SameSite=Strict` Cookie（生产模式下额外加 `Secure`）。
- **限流**（`app/middleware/rate_limit.py`）：进程内按 IP 的滑动窗口（默认 60/分钟），只作用于非 GET/HEAD/OPTIONS 的写请求；本地回环地址默认豁免（可关）。`X-Forwarded-For` 仅在直连 IP 位于 `ALLOWED_PROXIES` 白名单内时信任。登录接口另有独立的按 (IP, email) 失败计数限流。

## 路由清单

### `/projects`（`app/api/projects.py`）

| 方法 路径 | 说明 |
|---|---|
| `POST /projects` | 创建项目（登录态下自动打 `owner_id`） |
| `GET /projects/{id}` | 详情（owner 校验） |
| `PUT /projects/{id}/platform` | 设置发布平台（fanqie/qidian/qimao/personal/none/internal，影响合规行为） |
| `GET /projects` | 列表，支持 `q`（标题/主角模糊）、`genre` 筛选 |

### 世界构建向导（`app/api/worldbuild.py` + `app/worldbuild/`）

| 方法 路径 | 说明 |
|---|---|
| `GET /worldbuild/stages` | 10 阶段元信息列表（前端渲染用，1 小时缓存） |
| `POST /projects/{id}/worldbuild/start` | 创建 `GenerationJob`，后台任务跑 `run_worldbuild_job` |
| `GET /projects/{id}/worldbuild/stream?job_id=` | SSE：`stage_start`/`stage_done`/`job_done`/`job_failed` |
| `GET /projects/{id}/worldbuild/result` | 世界观/人物/关系/势力/力量体系/地图/伏笔/货币全量 + 一致性告警 |

**10 阶段流程**（`app/worldbuild/stages.py`，纯 `for` 循环，未用 LangGraph——刻意选择简单实现）：

| # | 阶段 key | 说明 |
|---|---------|------|
| 1 | `parse_config` | 纯本地解析 `Project.config_json`，无 LLM |
| 2 | `world_basics` | LLM（角色 `structured_logic`）生成 7 大板块世界观 + 故事核心 + 历史时间线，JSON Schema 校验 |
| 3 | `plot_skeleton` | 3-5 卷大纲骨架 |
| 4 | `characters` | LLM（角色 `creative_detail`）生成 4-6 个角色，每个含 8 部分结构化卡片 |
| 5 | `relations` | 富文本人物关系边（互相关系/强度/标签/演变/关键事件），校验失败则降级而非中断 |
| 6 | `foreshadowing` | 伏笔条目，按人物名关联 |
| 7 | `map` | 地图节点树（`parent_name` 解析） |
| 8 | `factions_power` | 势力 + 力量体系分级（含突破条件、修炼耗时） |
| 9 | `currency_special` | 货币 + 特殊设定 |
| 10 | `consistency_check` | 纯规则检测（无 LLM）：重名角色、孤立地图节点、未解决伏笔关联，及关系基数超标/无势力角色/力量体系孤儿三项新规则；仅告警不阻断 |

`mock` provider 模式下所有阶段均走内置 `mock_payload` 兜底，可离线跑通全流程用于测试。

### `/projects/{id}/chapters`（`app/api/chapters.py`）

| 方法 路径 | 说明 |
|---|---|
| `GET /chapters` | 列表，80 字预览 |
| `GET /chapters/search?query=&character_id=&top_k=` | 语义检索（须在 `/{chapter_id}` 之前注册以避免路由冲突） |
| `GET /chapters/{id}` | 章节详情 + 出场角色 |
| `GET /chapters/{id}/characters` | 出场角色列表 |
| `POST /chapters` | 创建章节 → 触发 embedding、角色打标、重复率检查 |

### `/providers`、`/role-assignments`

`GET/POST/PUT/DELETE /providers`（创建/更新时加密 `api_key`，删除时先置空依赖的 `RoleAssignment.provider_id`）；`GET /role-assignments`（15 角色解析后的 provider 信息）、`PUT /role-assignments/{role_key}`（重新绑定）。

`ROLE_REGISTRY`（`app/bridge/role_registry.py`）15 个角色：`structured_logic`、`creative_detail`、`consistency_check`（世界构建向导用）+ `orchestrator`、`planner`、`outline`、`writer`、`normalizer`、`compliance`、`checker_main`、`checker_cross1`、`checker_cross2`、`rewriter`、`tracker`、`summarizer`（写作引擎用）。

### 引擎桥接：`/projects/{id}/bridge`（`app/api/bridge.py`）

| 方法 路径 | 说明 |
|---|---|
| `GET/PUT /bridge/binding` | 读取/更新 `NovelAIBinding`（引擎工作目录绑定） |
| `POST /bridge/run` | 触发引擎命令；写类命令（`WRITE_COMMANDS = {planner, bootstrap, run, resume, init_arc}`）要求世界构建已完成；DB 层检查避免同项目并发运行 |
| `GET /bridge/stream?run_id=` | SSE 转发子进程 stdout（`log`/`start`/`complete`/`error`/`done`） |
| `POST /bridge/push-concept` | 项目配置 → 写入引擎 `config/novel_config.json` |
| `POST /bridge/pull-setting` | 读取引擎 `output/setting_package.json`，Schema 校验后拆解落库（子表先删后插，保证外键安全） |
| `POST /bridge/import-chapters` / `reimport-chapters` | 读取引擎输出章节 txt+meta，入库/覆写 |
| `GET /bridge/status` / `pending` / `budget` | 读取引擎 `orchestrator_state.json` / `budget_log.jsonl` |
| `POST /bridge/review` | 人工审核（accept/reject/edit）弹出的 `human_pending` 任务 |
| `POST /bridge/set-audit-mode` | 持久化项目级 `audit_mode`（full/lite/draft），下次子进程启动时生效 |

**子进程调用机制**：`_spawn_engine_subprocess`（bridge.py:186）用 `subprocess.Popen` 拉起 `python engine/workers/run_bridge_subprocess.py <run_id> <project_id> <command> <args...>`，注入 `NOVEL_OUTLINE_MODE`、`NOVEL_AUDIT_MODE`、`NOVEL_AI_DIR`、`NOVEL_ENGINE_MOCK` 环境变量。后台守护线程逐行捕获 stdout 写入 `BridgeRun.stdout_text`（每 50 行 flush）并推送 SSE 事件，进程退出后置 `status=done/failed`。详见 [03-Writing-Engine.md](03-Writing-Engine.md#从后端调用引擎)。

### 其他

- `/projects/{id}/rules`（`app/api/rules.py`）：`GET/PUT rules`（风格/禁忌词/模板）、`POST rules/post-process`（logic/venom/deai 三种后处理工具，直接单次调用 `engine.llm_router` 的 LLMRouter，LLM 失败返回 503 而非假通过）。
- `/projects/{id}/foreshadowings`（`app/api/foreshadowings.py`）：伏笔列表 + 状态更新（未铺垫/已铺垫/已回收）。
- `/projects/{id}/ai-assist-level`（`app/api/ai_assist.py`）：AI 辅助程度标注（ai_assisted/human_primary/unset），对应 2025-09-01 生效的 AI 内容标识监管要求。
- `/projects/{id}`（`app/api/world.py`）：`GET worldview/rich`、`characters`、`characters/{id}`、`characters/{id}/relations`、`relations/graph`（人物关系图数据）。

## RAG 子系统（`app/rag/`）

- **`embedding.py`**：`embed_text()` 按配置解析 provider——显式 `mock` 走确定性 256 维字符二元组哈希向量；未配置 `embedding_api_key` 时自动降级为 mock（离线开发友好）；否则调用真实 embedding API（默认 DashScope `text-embedding-v3`）。`cosine_similarity()` 维度不匹配时返回 0.0（安全失败而非报错/误判）。
- **`retrieval.py`**：`add_chapter()` 插入章节、按子串命名匹配打 `ChapterCharacter` 标签、embedding 入库、跑 `check_repetition()`（余弦相似度 ≥0.85 视为重复率过高，仅告警不阻断，对标网文平台"重复率"审核）。`semantic_search_chapters()` 先按角色出场章节做图谱过滤，再按向量相似度排序——图谱精确缩小范围，向量做模糊相关性排序，不用于精确事实核查（那是一致性扫描的职责）。

## 数据契约（`backend/schema/`）

5 个 JSON Schema（Draft 7）：`setting_package.schema.json`、`chapter_meta.schema.json`、`world_view_rich.schema.json`、`character_card.schema.json`、`entity_relation_rich.schema.json`，由 `app/schema_validator.py` 加载。历史上曾因 Planner 输出字段名与 `setting_sync.py` 消费端字段名漂移，导致 5 张表静默留空，加入这套 Schema 校验后杜绝此类问题。

## 关键依赖

`fastapi` 0.115、`uvicorn[standard]` 0.30.6、`sqlalchemy` 2.0.35、`pydantic` 2.9.2、`httpx` 0.27.2、`sse-starlette` 2.1.3、`bcrypt` ≥4.0、`PyJWT` ≥2.8、`jsonschema` ≥4.0、`tenacity` ≥8.1,<10（LLM 重试装饰器）、`langgraph` ≥0.2 + `langgraph-checkpoint-sqlite` ≥3.0、`anthropic` ≥0.40、`jieba` ≥0.42（中文分词）、`alembic` ≥1.13（脚手架，日常 schema 演进走 `app/migrations.py` 而非 `alembic upgrade head`）。
