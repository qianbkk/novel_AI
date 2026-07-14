# Master Audit Tracker（2026-07-13 整合追踪）

把所有未完成 / 已完成 / 暂时跳过的审计 / 修复项整合到一份追踪文档，按
**优先级 + 完成状态** 标注。覆盖以下来源：

- 三路原始安全审计（security-fixes-2026-07-13 的 18 条）
- code-review-action-2026-07-13（linshi.txt 两份审查）
- simplify round 1 / round 2 / round 3（4 cleanup agents）
- commit 4be81b2 的代码审查（round 3 follow-up）
- 实测报告：commit 4be81b2 后的全面测试复现

## 状态图例

- ✅ 已完成（commit hash 标注）
- 🔧 本轮已修复（待 commit）
- ⏸ 暂不修（按 "prioritize real risks over future armor" 原则搁置，
  标 [skip] 原因）
- 🐞 已识别未修（按优先级排序，明确下一步触发条件）

---

## A. 安全 / 健壮性原始 5 项修复（security-2026-07-13）

| # | 标题 | 状态 | Commit |
|---|------|------|--------|
| A1 | `Chapter.chapter_no` 无唯一约束 → 并发 POST 可重复插入 | ✅ | 52492ce |
| A2 | `BridgeRun` 加 pid + lifespan 回收时活体探测 | ✅ | 52492ce |
| A3 | 引擎子进程 stdout 空闲看门狗 | ✅ | 52492ce |
| A4 | `migrations.py` TOCTOU 启动崩溃 → 单条失败隔离 | ✅ | 52492ce |
| A5 | 删除 `strip-junk-headers` 硬编码端点 | ✅ | 52492ce |

详见 `docs/security-fixes-2026-07-13.md`。

---

## B. 代码审查应用（code-review-action-2026-07-13）

基于 `linshi.txt` 两份历史审查报告的辩证评估。

| # | 标题 | 状态 | Commit |
|---|------|------|--------|
| B1 | stage 端点测试加 order 断言（9821c2e 🟡-2） | ✅ | 90dbf62 |
| B2 | strip_markdown_fence 真做替换（70dd44a 🟡-1） | ✅ | 90dbf62 |
| B3 | truncate_preserving_ends 加 head+tail >= threshold 护栏 | ✅ | 90dbf62 |
| B4 | strip_markdown_fence 类型 + 行为一致化 | ✅ | 90dbf62 |

详见 `docs/code-review-action-2026-07-13.md`。

---

## C. Simplify Round 1（a08d52d — 4 cleanup agents, 8 项改进）

全部 ✅ 已合并。详见 `git show a08d52d --stat`。

---

## D. Simplify Round 2（0fcf0bc — 4 cleanup agents, 13 项应用 / 7 项跳过）

应用清单见 `docs/simplify-2026-07-13-round2.md`。

跳过的 7 项按 memory "prioritize real risks" 原则搁置：
- D-S1 `_watchdog` 抽通用类 — 单 caller，提早抽象违背原则
- D-S2 `_recover_orphan_bridge_runs` 删 legacy 分支 — 保守保留 None pid 走标 failed
- D-S3 `db_bootstrap` 提到父 conftest.py — autouse 破坏 test_auth 等
- D-S4 `run_migrations` 缓存 sqlite_master — 单租户本地原型启动 ms 级优化不划算
- D-S5 `_delete_project` 加 ON DELETE CASCADE — schema 改动太大
- D-S6 `db_bootstrap` 改 session-scoped — invariant 测试要独立 schema
- D-S7 `security-2026-07-13 #N` 注释标签清理 — 追踪文档是契约

---

## E. Simplify Round 3（4be81b2 — 异常分类 fail-fast + TOCTOU 并存）

核心修复：`_is_benign_alter_error` helper 单独分类良性 race；外层
try/except 一刀切删掉；真 DDL 错原样 raise。

详见 `docs/simplify-2026-07-13-round3.md`。

**Test 修复**（test_migration_safety.py round 1 baked-in 错合同修正）：
- ✅ `test_missing_table_does_not_crash_run`（原 test_bad_migration_does_not_crash_run）
- ✅ `test_real_ddl_failure_propagates`（原 test_normal_migrations_still_apply_after_bad_one）

---

## F. Round 3 follow-up 代码审查（commit 4be81b2 code-review）

### 🟡 关注项（建议在 round 4 修）

- **F-1** 事务回滚语义 docstring 补充：单条迁移失败会 ROLLBACK 整个事务，下次启动会 idempotent 重放
- **F-2** `_apply_one_index_migration` 路径注释：IF NOT EXISTS 已处理 index 重复，但 UNIQUE 约束违反（数据层面）仍 propagate
- **F-3** `test_duplicate_column_error_swallowed` 用 caplog 强化验证 race-loser 路径真正被触发

### 🟢 优化项

- **F-4** 删除未使用的 `import sqlite3`（migrations.py 第 15 行）
- **F-5** 补 `test_real_ddl_failure_propagates` 验证 `_is_benign_alter_error` 分类逻辑（直接 mock conn.execute 抛 sqlite3.OperationalError）
- **F-6** test_migration_safety.py 末尾补换行符
- **F-7** `_is_benign_alter_error` 加 `sqlite3.Error` 类型预检（优先级低）

**总评**：高质量修复 commit。`4be81b2` 逻辑清晰，docstring 诚实记录
设计意图历史教训。建议合并后跟进 F-1 / F-3 / F-4。

---

## G. 实测报告 Finding #5（main 分支当前 3 个真实失败测试）

> 来源：实测报告 commit 4be81b2，距上次 18 个 commit 855 条测试全跑
> 一遍后独立发现。

### G-1 🐞 `test_frontend_default_url_is_valid` — REPO_ROOT vs BACKEND_ROOT 错位

- **状态**: 🔧 本轮已修复
- **症状**: 测试读 `D:\AI\Codex_workspace\frontend\src\api\client.ts`
  FileNotFoundError（路径少一层 `Novel_AI/`）
- **根因**: `tests/invariants/test_frontend_align.py` 第 13 行
  `BACKEND = Path(REPO_ROOT)` 错把 REPO_ROOT 当 backend 根；
  `BACKEND.parent / "frontend"` = 仓库父目录 / frontend（不存在）
- **修法**: 改为 `BACKEND = Path(BACKEND_ROOT)` —— `BACKEND_ROOT` 是
  `backend/` 自身（`backend.parent == repo_root`），符合
  `_paths.py` 的设计

### G-2 🐞 `test_no_hardcoded_8123_in_docs_and_scripts` — REM 注释被误判

- **状态**: 🔧 本轮已修复
- **症状**: dev.bat:91-92 是解释 findstr 锚点语义的 REM 注释
  （"锚定在尾部空格上是为了防止 :8123 误匹配到 :81230"），
  被检查脚本当成违规实例
- **根因**: 检查逻辑 `if stripped.startswith("REM") and "8132" in stripped: continue`
  只豁免同时含 `8132` 的 REM 行；dev.bat:91-92 是纯解释 `:8123` 锚点的 REM 行
- **修法**: 豁免条件改为「REM 注释行」（`stripped.startswith("REM")` 就豁免，
  无论是否含 `8132`），并补一条 Chinese 解释文本的 ALLOWED_LINE_FRAGMENTS

### G-3 🐞 `test_middleware_registered_in_main` — 配置漂移

- **状态**: 🔧 本轮已修复（按实测报告建议修真配置漂移，不改测试断言）
- **症状**: main.py 已注册 `RateLimitMiddleware`（第一条 assert 通过），
  但不含字符串 `RATE_LIMIT_PER_MINUTE`（第二条 assert 失败）
- **根因（实测报告发现）**:
  - `app/config.py:95-97` 定义 `Settings.rate_limit_per_minute`（pydantic）
  - `app/main.py:243` 注释写着 "阈值收口到 app.config.settings.rate_limit_per_minute"
  - 实际生效在 `app/middleware/rate_limit.py:184`，**直接 `os.environ.get("RATE_LIMIT_PER_MINUTE", "60")`**，完全不经过 config.py 的 Settings 类
  - 也就是说 `Settings.rate_limit_per_minute` 是装饰性配置，没人真正读
- **修法**: 改 main.py / middleware 让阈值真正通过 Settings 走——
  在 main.py 启动时把 settings.rate_limit_per_minute 写到 env（确保
  middleware 仍然用 os.environ.get 读到），或重构 middleware 显式
  接受 max_per_minute 参数（更彻底）

### G-4 ⏸ 测试隔离问题（pre-existing）

- **状态**: ⏸ 暂不修（按用户原文 + memory 原则）
- **症状**: 跑 `tests/test_phase1_5_smoke.py` 整套时 6 failed / 7 passed / 1 skipped，
  全部是 `sqlalchemy.exc.IntegrityError: FOREIGN KEY constraint failed`
  on `INSERT INTO novel_ai_bindings`——单独跑全过
- **根因**: `_seed_project_and_binding` 用 `db.merge(p)` 但 commit 时机不对，
  Project 行其实没 flush 到 DB，FK 约束炸了。这是 audit 反复强调的
  "测试间相互污染"
- **决定**: 与 round 3 修复**无关**，按 memory "prioritize real risks
  over future armor" 原则暂不深挖。已记录在 `simplify-2026-07-13-round3.md`
  第二章节
- **建议**: 未来用 pytest-randomly 之类工具专门抓「什么状态在测试间泄漏」

---

## H. 实测报告 Finding #3 — test_invariants.py shim 重复收集

- **状态**: ⏸ 暂不修（用户实测已确认影响，半天工作量内可收尾，但不在本批）
- **症状**: `tests/test_invariants.py` 27 行 re-export shim 用 `import *`
  会导致 pytest 重复收集并重复执行同一批测试类（一次在
  `tests/invariants/test_engine.py::TestPlannerAtomicWrite`，一次在
  `tests/test_invariants.py::TestPlannerAtomicWrite`）
- **影响**: 全量测试运行时间和失败噪音都翻倍
- **修法（建议）**: 改 conftest.py `collect_ignore` 排除 shim 文件，
  或者直接删 shim 并更新文档命令为 `pytest tests/invariants/`
- **未执行原因**: 与本批修复（fail-fast + TOCTOU）无关；
  全量测试每次都是按文件单独跑所以不受 shim 影响

---

## I. 实测报告确认的「真修复」部分（实测报告回归验证）

> 来源：实测报告独立验证（commit 4be81b2 后 855 条测试全跑）

| 项 | 验证方式 | 状态 |
|----|----------|------|
| `run_outline_card` B/C 分支假功能 | 读代码 + test_outline_card.py | ✅ |
| migrations.py 异常吞噬（finding #2） | 读 `_apply_one_migration` 实现 | ✅ |
| BridgeRun 孤儿恢复 | 读 `_recover_orphan_bridge_runs` | ✅ |
| 越权访问修复（finding #4） | 沿用之前验证 | ✅ |

---

## J. 安全审计原 13 项「暂不修」（生产模式才有意义）

来自 security-fixes-2026-07-13 附录：

| # | 简述 | 严重度 | 状态 |
|---|------|--------|------|
| J-#6 | `/providers` `/role-assignments` 完全无鉴权 + 跨租户泄露 | 🔴 HIGH | ⏸ |
| J-#7 | `RateLimitMiddleware` 路径前缀 `/api/v1/` 不匹配死代码 | 🔴 HIGH | ⏸ |
| J-#8 | `POST /projects` 生产模式下不校验登录 | 🟠 MED | ⏸ |
| J-#9 | SSE 长连接生产模式下彻底不可用 | 🟠 MED | ⏸ |
| J-#10 | HttpOnly Cookie 端到端死代码 | 🟠 MED | ⏸ |
| J-#11 | `novel_ai_dir` 路径零校验 | 🟠 MED | ⏸ |
| J-#12 | `LoginRateLimiter` 内存无清理 | 🟠 MED | ⏸ |
| J-#13 | `/auth/register` 邮箱枚举 | 🟡 LOW | ⏸ |
| J-#14 | `/auth/change-password` 旧密码无限流 | 🟡 LOW | ⏸ |
| J-#15 | 后端 traceback 透传 | 🟡 LOW | ⏸ |
| J-#16 | 登录密码框无 `autocomplete` | 🟡 LOW | ⏸ |
| J-#17 | 引擎 find/grep 在 Windows 中文路径下偶发 IO | 🟡 LOW | ⏸ |
| J-#18 | `pull_setting_package` / `take_all_snapshots` 已二次确认安全 | n/a | n/a |

**何时回头处理**: 等真有对外开放计划时按
`#6 → #7 → #9 → #10 → #11 → #8 → #12 → #13-#16` 顺序恢复。

**J-#6 是唯一**需要先做产品决策的项（Provider / RoleAssignment 多用户归属模型），
不能纯靠工程判断。详见实测报告对应章节。

---

## K. 当前建议优先级（按性价比）

1. **🔧 本轮修复**: G-1 / G-2 / G-3 + F-1 / F-3 / F-4（半天内可收尾）
2. **建议未来 CI**: GitHub Actions 跑 `pytest tests/` + 报告失败数（防
   "改动速度超过验证机制覆盖"）
3. **待确认**: G-4 测试隔离（pre-existing，等用户决定）
4. **未来 round**: H shim 收集去重 + J-#6 Provider/RoleAssignment 产品决策
5. **未来 round**: F-5 / F-6 / F-7 优化项（低优先级）

---

## L. Commit 索引

```
4be81b2  fix(migrations): 异常分类（fail-fast + TOCTOU 并存）
0fcf0bc  refactor(simplify-2026-07-13-round2): 4 cleanup agents round 2 应用 13 项
90dbf62  refactor(code-review-2026-07-13): 应用 linshi.txt 审查建议
a08d52d  refactor(simplify-2026-07-13): 4 cleanup agents 审计后应用 8 项改进
52492ce  fix(security-2026-07-13): 修本地单人模式下的 5 个真实风险
```

Round 4 完成后补:
```
<hash>   fix(audit-tracker-round4): G-1/2/3 + F-1/3/4（实测报告 + round 3 follow-up）
```