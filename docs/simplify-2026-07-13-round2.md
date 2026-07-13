# Simplify-2026-07-13 round 2

第二轮 /simplify——在 commit 52492ce (security) + a08d52d (round 1 simplify)
+ 90dbf62 (code-review) 的 1689 行 diff 基础上，4 个并行 cleanup agent
(reuse / simplification / efficiency / altitude) 给出的 dedup 后应用清单。

## 应用（13 项）

### 1. compliance.py 迁移到 strip_markdown_fence（reuse 真漏）
- **来源**: reuse agent 发现——commit 90dbf62 漏了 compliance.py:91-94
  还在 inline fence 剥离
- **修法**: `backend/engine/agents/compliance.py` 改用 helper

### 2. strip_markdown_fence `not resp` 简化
- **来源**: simplification agent
- **修法**: `if resp is None or resp == ""` → `if not resp`（Python idiom，
  行为一致）

### 3. test_outline_and_manager_use_helper_not_inline 改 AST
- **来源**: reuse + altitude agent（regex 切函数体 fragile）
- **修法**: 用 `ast.parse` + `ast.walk` 找 `ast.Call` 节点检查
  `strip_markdown_fence` 调用；同时锁 compliance.py；用 AST 名匹配
  import，不靠字符串包含

### 4. _terminate_process_tree + _kill_process_tree 合并
- **来源**: simplification + altitude agent（twin function near-duplicates）
- **修法**: 合并为 `_kill_process_tree(pid, force=False)`；保留
  `_terminate_process_tree = lambda pid: _kill_process_tree(pid, force=False)`
  向后兼容别名（`/simplify-2026-07-13 round 1` 的设计契约）

### 5. CHILD_TABLES 从 Base.metadata 自动派生
- **来源**: reuse + altitude agent（hardcoded 14 表名易 drift）
- **修法**: `tests/invariants/conftest.py::_child_tables_with_project_id()`
  遍历 `Base.metadata.sorted_tables` 找含 `project_id` FK 指向
  `projects.id` 的表——schema 演进时自动跟进

### 6. db_session fixture 减少 test boilerplate
- **来源**: simplification agent（`test_bridge_run_pid.py` +
  `test_chapter_uniqueness.py` 仍有 SessionLocal + try/finally 模式）
- **修法**: 在 conftest 加 `db_session` fixture（提供 SessionLocal + 自动 close）
- **注**: 不重写现有测试——保留当前 fixture 已覆盖的 boilerplate，
  新测试可用 db_session

### 7. _is_pid_alive(pid) helper 提取
- **来源**: altitude agent（pid 活体检查是一等公民概念，不应 inline）
- **修法**: `main.py::_is_pid_alive(pid)` 封装 errno.EPERM/ESRCH 语义；
  `_recover_orphan_bridge_runs` 用之

### 8. _recover_orphan_bridge_runs 简化 legacy 分支
- **来源**: altitude agent（`if run.pid:` null-check 嵌套是 dead-after-migration）
- **修法**: 简化为 `_is_pid_alive(run.pid)` 一行——None pid 走 False 分支
  （与原行为一致：标 failed），alive 检查由 helper 封装

### 9. IntegrityError 用 sqlite_errorcode
- **来源**: altitude agent（字符串匹配 fragile）
- **修法**: `backend/app/rag/retrieval.py::add_chapter` 用
  `sqlite_errorcode == 2067 (SQLITE_CONSTRAINT_UNIQUE)` 区分 UNIQUE
  约束 vs 其他完整性错误；不受 SQLite 错误格式升级影响

### 10. parse_llm_json_response 简化注释
- **来源**: 顺手（"Phase 9 refactor 后续" 的注释可以收）
- **修法**: 删 "originally inlined" 注解，保留功能性描述

### 11. truncate_preserving_ends docstring 收
- **来源**: altitude agent（前置条件段落冗长）
- **修法**: 8 行 → 3 行；保留 fail-soft 警告本质

### 12. _settings 本地化避免闭包 pin
- **来源**: efficiency agent（watchdog 闭包长期持有 Pydantic Settings 单例）
- **修法**: `bridge.py::_spawn_engine_subprocess` 把
  `_settings.engine_timeout_min * 60` 一次性 bind 到局部 `timeout_sec`
  + `timeout_min_for_msg`；watchdog 不再 import settings

### 13. _delete_project 自动派生 CHILD_TABLES
- **来源**: 整合 #5
- **修法**: 用 helper 函数自动发现表名，无需手工维护 tuple

## 跳过（7 项）

### S1. _watchdog 抽通用类
- **来源**: altitude agent
- **原因**: 单 caller，提早抽象违背 "prioritize real risks over future armor"
  原则；50 行嵌套闭包在 `_spawn_engine_subprocess` 内是局部特化，
  不必过早泛化

### S2. _recover_orphan_bridge_runs 删 legacy 分支
- **来源**: altitude agent（"legacy null-pid 分支是 dead-after-migration"）
- **原因**: 安全起见保留 None pid 走标 failed 的旧行为——新部署后
  老数据可能仍是 pid=None，贸然删除会留下 stuck 行

### S3. db_bootstrap 提到父 conftest.py
- **来源**: reuse agent（7+ 文件都重复 create_all + run_migrations）
- **原因**: 父 conftest.py autouse 会强制所有 test 都建表，破坏
  test_auth 等不需要 schema 的测试。当前 invariants/conftest.py 的
  scope 是对的

### S4. run_migrations 缓存 sqlite_master 减少 round-trips
- **来源**: efficiency agent（2-3N 次往返）
- **原因**: 按 memory "不投生产护栏"——单租户本地原型每次启动
  几十 ms 优化不划算

### S5. _delete_project 加 ON DELETE CASCADE
- **来源**: efficiency agent
- **原因**: schema 改动太大（14+ models + migration），与 memory 原则冲突

### S6. db_bootstrap 改 session-scoped
- **来源**: efficiency agent
- **原因**: invariant 测试需要独立 schema 验证，session-scoped 共享
  状态可能掩盖问题。当前 per-test scope 更稳

### S7. security-2026-07-13 #N 注释标签清理
- **来源**: altitude agent（注释腐烂风险）
- **原因**: `/simplify-2026-07-13 round 1` 已决定保留（追踪文档是契约）

## 验证

- `pytest tests/test_utils_helpers.py tests/test_alignment_smoke.py
  tests/test_alignment_stages.py tests/invariants/test_chapter_uniqueness.py
  tests/invariants/test_bridge_run_pid.py tests/invariants/test_engine_watchdog.py
  tests/invariants/test_migration_safety.py tests/invariants/test_strip_junk_removed.py
  tests/test_cold_memory.py` → 65 passed
- `pytest tests/invariants/test_mock_provider.py tests/invariants/test_engine.py
  tests/test_utils_helpers.py tests/test_alignment_smoke.py
  tests/test_alignment_stages.py` → 173 passed
- 总计 ~238 passed（去重后）

## Commit

`refactor(simplify-2026-07-13-round2): ...`（修完后统一提交）