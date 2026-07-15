# 安全/健壮性修复追踪（2026-07-13）

三路审查发现的 18 个问题，按"对当前 dev 单人使用是否真实风险"分为两批：

- **本批修**（dev 单人模式下也会咬到的坑，5 条）
- **暂不修**（要开放多租户 `NOVEL_PRODUCTION=1` 才有意义，13 条；参见文末附录）

## 本批修复（5 项已全部完成）

| # | 标题 | 状态 | Commit |
|---|------|------|--------|
| #1 | Chapter.chapter_no 无唯一约束 | ✅ | 见下方 |
| #2 | BridgeRun 加 pid + 回收时活体探测 | ✅ | 见下方 |
| #3 | 引擎子进程 stdout 空闲看门狗 | ✅ | 见下方 |
| #4 | migrations.py TOCTOU + 单条失败隔离 | ✅ | 见下方 |
| #5 | 删除 strip-junk-headers 硬编码端点 | ✅ | 见下方 |

### #1 `Chapter.chapter_no` 无唯一约束

- **状态**: ✅ 已修复（2026-07-13）
- **现象**: `Chapter.chapter_no` 是普通 `Integer`，无 `unique=True`。并发 `POST /chapters`
  同号时两条都成功，破坏排序 + RAG 去重逻辑（`chapter_import.py` 是按 `chapter_no`
  幂等去重的，但 SQL 层没有兜底）。
- **修法**:
  1. `backend/app/models.py` Chapter 模型加 `__table_args__ = (UniqueConstraint("project_id", "chapter_no", name="uq_chapters_project_chapter_no"),)`
  2. `backend/app/migrations.py` 新增 `_UNIQUE_INDEX_MIGRATIONS` + `_index_exists()` helper，
     启动时幂等 `CREATE UNIQUE INDEX IF NOT EXISTS`（已存在数据库也要补建约束）
  3. `backend/app/rag/retrieval.py` `add_chapter()` catch IntegrityError → 抛
     `DuplicateChapterError(existing_chapter_id)` 让 API 层返回有用信息
  4. `backend/app/api/chapters.py` POST 路由 catch DuplicateChapterError → 409 +
     `{code: "duplicate_chapter_no", existing_chapter_id}`
  5. `backend/tests/invariants/test_chapter_uniqueness.py` 5 个用例：索引存在性 / DB 层
     兜底 / 跨项目允许同号 / DuplicateChapterError 行为 / API 409
- **验证**: `pytest tests/invariants/test_chapter_uniqueness.py -v` → 5 passed；
  `pytest tests/invariants/test_chapter_uniqueness.py tests/test_alignment_stages.py tests/invariants/test_schemas.py` → 54 passed
- **Commit**: 包含本次修改（修完后统一提交）

### #2 `uvicorn --reload` 重启时引擎子进程无 PID 追踪 → 双写风险

- **状态**: ✅ 已修复（2026-07-13）
- **现象**: 引擎设计为独立 OS 进程（这样 `uvicorn --reload` 重启后端不打断写作任务）；
  但 `BridgeRun` 表只存了 started_at / ended_at / status，没有存子进程 PID。
  `_recover_orphan_bridge_runs` 在 lifespan 启动时把所有"重启时尚在跑"的状态为
  `running` 的行一律标 `failed`，但**没去校验子进程是否真的还在**。结果：
  - 重启后新启动的 uvicorn 看到表里有一条 `running` 的旧行 → 立即把它标 failed
  - 同时旧子进程根本没收到 SIGTERM，还在继续写磁盘
  - 用户此时点击"运行" → 第二个子进程起来 → 两个子进程写同一个 `novel_ai_dir`
  - 双写 = setting_package.json / orchestrator_state.json / 章节 txt 全部损坏
- **影响**: 这是 dev.bat 默认工作流（`uvicorn --reload`）下几乎一定会撞到的最严重的坑。
- **修法**:
  1. `backend/app/models.py` `BridgeRun` 加 `pid: Integer NULL` + `pgid: Integer NULL`
  2. `backend/app/migrations.py` `_MIGRATIONS` 加 `("bridge_runs", "pid", "INTEGER")` 和 `("bridge_runs", "pgid", "INTEGER")` — 已有数据库自动 ADD COLUMN
  3. `backend/app/api/bridge.py` `_spawn_engine_subprocess` 用 `start_new_session=True` 让 subprocess 独立进程组，
     启动后把 `proc.pid` + `os.getpgid(proc.pid)` 写进 BridgeRun 行（status='running' 时同时落库）
  4. `backend/app/main.py` `_recover_orphan_bridge_runs(project_id=None)` 用 `os.kill(pid, 0)` 探测活体——
     还活着的行**完全不动**（让 POST /bridge/run 的 (pending, running) 检查自然 409），
     已死的才标 failed；支持可选 project_id 过滤供测试用
  5. `backend/tests/invariants/test_bridge_run_pid.py` 3 个用例：列存在性 / 活体/死亡/legacy 三态分流 / pgid=None 兼容性
- **验证**: `pytest tests/invariants/test_chapter_uniqueness.py tests/invariants/test_bridge_run_pid.py tests/invariants/test_schemas.py tests/invariants/test_bridge.py -v` → 88 passed
- **Commit**: 包含本次修改（修完后统一提交）

### #3 引擎子进程无超时/看门狗

- **状态**: ✅ 已修复（2026-07-13）
- **现象**: 引擎子进程跑一次"运行 N 章"任务，如果某次 LLM 调用死锁/无限重试，子进程
  永远不返回。后果：
  - `BridgeRun` 行卡 `running` 永远不变
  - SSE 消费线程永久占用 `ThreadPoolExecutor`（每次订阅都起一个）
  - 用户只能重启 uvicorn 重置（而重启又触发 #2）
- **修法**:
  1. `backend/app/config.py` 新增 `engine_timeout_min: int = 120` 字段（环境变量 `NOVEL_ENGINE_TIMEOUT_MIN`）
  2. `backend/app/api/bridge.py` 新增跨平台 `_kill_process_tree(pid, grace)` helper：
     - POSIX: `os.killpg(pid, SIGTERM/SIGKILL)`
     - Windows: `taskkill /T /PID` (grace=False 时加 `/F`)
  3. `_drain_stdout` 闭包内共享 `_activity["last_stdout_ts"]` dict；每次 readline 更新 ts
  4. 新增 `_watchdog` 守护线程：每 30s 轮询 `proc.poll()` + 空闲超时；
     超时先 `_kill_process_tree(grace=True)`，30s 宽限期后 `_kill_process_tree(grace=False)`
  5. `_drain_stdout` 退出时检查 `_activity["killed_by_watchdog"]`：被看门狗终止的标 failed(timeout) 并往 stdout_text 追加错误消息
  6. `backend/tests/invariants/test_engine_watchdog.py` 3 个用例：默认值 / 真实看门狗跨平台 killpg / 进程已死立即返回
- **验证**: `pytest tests/invariants/test_chapter_uniqueness.py tests/invariants/test_bridge_run_pid.py tests/invariants/test_engine_watchdog.py tests/invariants/test_bridge.py tests/invariants/test_schemas.py -v` → 91 passed
- **Commit**: 包含本次修改（修完后统一提交）

### #4 `migrations.py` TOCTOU 启动崩溃

- **状态**: ✅ 已修复（2026-07-13）
- **现象**: `run_migrations()` 走 `if not _column_exists: ALTER TABLE ADD COLUMN ...`
  两步；`--reload` 时新旧 uvicorn 进程会短暂并存，都读到"列不存在"，都尝试 ALTER，
  后提交的那个抛 `duplicate column name`，**且当前代码无 try/except**，直接让 startup
  抛异常 crash。
- **影响**: dev 工作流每天会撞几次（每次改模型改 schema 重启都会触发一次）。
- **修法**:
  1. `backend/app/migrations.py` 抽出 `_apply_one_migration` / `_apply_one_index_migration`
     helper（让 try/except 不污染主流程可读性）
  2. ALTER 撞 `duplicate column` / `already exists` → log info + return False（race-loser 成功）
  3. `run_migrations` 两个 for 循环各包一层 `try/except`：单条真实 DDL 失败 → log warning + continue，不让 startup 崩溃
  4. `backend/tests/invariants/test_migration_safety.py` 3 个用例：duplicate column 竞态 / 坏 migration 不 crash / 坏 migration 后的好 migration 继续跑
- **验证**: `pytest tests/invariants/test_migration_safety.py tests/invariants/test_schemas.py -v` → 45 passed
- **Commit**: 包含本次修改（修完后统一提交）

### #5 `strip-junk-headers` 是硬编码路径的通用 API

- **状态**: ✅ 已修复（2026-07-13）
- **现象**: `POST /projects/{id}/bridge/strip-junk-headers` 在 `bridge.py` 里硬编码
  `data/engine/output/chapters` 和 `../novel_AI/output/chapters`，跟传入的
  `project_id` / `NovelAIBinding.novel_ai_dir` 没有任何关系。误点此按钮会改写固定目录
  的文件（破坏另一个项目）。
- **修法**:
  1. `backend/app/api/bridge.py` 删除 `strip_junk_headers` 路由定义，替换为说明性
     注释（指向 CLI 替代路径）
  2. `docs/wiki/02-Backend-API.md` 移除 `strip-junk-headers` 行
  3. `backend/scripts/export_openapi.py` 注释里加上"已删除"标记
  4. `scripts/strip_chapter_headers.py` 本身保留（CLI 用户仍可手动跑）
  5. 前端 `grep` 确认无任何 strip-junk 引用（无需改）
  6. `backend/tests/invariants/test_strip_junk_removed.py` 3 个用例：路由表无端点 /
     POST 返回 404 / CLI 脚本仍可 import
- **验证**: `pytest tests/invariants/test_strip_junk_removed.py -v` → 3 passed；
  `pytest <all 5 fix tests> + test_bridge + test_schemas -v` → 97 passed
- **Commit**: 包含本次修改（修完后统一提交）

---

## 修复流程

每修一项:
1. 改代码 + 写 / 改测试
2. 跑相关测试（功能测试必须绿）
3. 改本文件标记 ⬜ → ✅ + 记录 commit hash
4. `git add` + `git commit -m "fix(security-2026-07-13): #N 简述"`
5. 全部完成后一次 push

---

## 附录：暂不修（生产模式 `NOVEL_PRODUCTION=1` 才有意义的项）

| 编号 | 简述 | 严重度 |
|------|------|--------|
| #6 | `/providers` `/role-assignments` 完全无鉴权 + 跨租户数据泄露 | 🔴 HIGH |
| #7 | `RateLimitMiddleware` 是死代码（路径前缀 `/api/v1/` 不匹配） | 🔴 HIGH |
| #8 | `POST /projects` 生产模式下不校验登录 | 🟠 MED |
| #9 | SSE 长连接生产模式下彻底不可用（EventSource 不能带 Auth header） | 🟠 MED |
| #10 | HttpOnly Cookie 端到端死代码（后端不读 + 前端不带 + CORS 不开） | 🟠 MED |
| #11 | `novel_ai_dir` 路径零校验，可指向任意目录 | 🟠 MED |
| #12 | `LoginRateLimiter` 内存无清理（可被 (IP, 大量邮箱) 撑爆） | 🟠 MED |
| #13 | `/auth/register` 邮箱枚举 + 无注册限流 | 🟡 LOW |
| #14 | `/auth/change-password` 旧密码验证无限流 | 🟡 LOW |
| #15 | 后端报错 traceback 透传到前端 | 🟡 LOW |
| #16 | 登录密码框无 `autocomplete` 属性 | 🟡 LOW |
| #17 | 引擎 `find / grep` 在 Windows 中文路径下偶发 IO 错误（独立版相关，非后端） | 🟡 LOW |
| #18 | `pull_setting_package` / `take_all_snapshots` 已二次确认安全，**无需修** | n/a |

何时回头处理：等 #1-#5 修完 + 用户确认仍有对外开放的真实计划时再讨论。届时按
`#6 → #7 → #9 → #10 → #11 → #8 → #12 → #13-#16` 顺序恢复（#6 是当前最严重的，按
"未来护栏"原则暂搁置但留文档警告）。