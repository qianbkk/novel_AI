# Simplify-2026-07-13 round 3

第三轮 /simplify——直接复用用户在对话最末给出的全面复审结论
（"全面复审结论：0fcf0bc"），按 user-marked 优先级处理：

1. **真实问题（必须修）**：迁移异常分类，fail-fast vs TOCTOU 现在互相矛盾
2. **真实问题（记一笔，不深挖）**：测试隔离（17 全量失败 / 单独跑过）
3. **产品决策（等真要上线再处理）**：Provider / RoleAssignment 多用户归属模型
4. **其余暂不修项**：复核判断合理，保留

## 真实修复 1：迁移异常分类（核心）

### 问题（用户原文）

迁移系统的「fail-fast」和「TOCTOU 安全」两个目标现在互相矛盾——
`run_migrations` 外层 `except Exception` 把 `_apply_one_migration` 抛出的
真 DDL 错也吞了，导致 `test_phase1_5_smoke.py::test_migration_fail_fast_on_ddl_error`
回归失败（这是 Phase 3 共识 finding #2 的回归）。

之前 round 1 简化的设计意图（外层 try/except 兜单条失败）是错的——
和 `_apply_one_migration` 内层「benign race 才吞，其他 raise」配合后，
**外层兜底反向把真 DDL 错也吞了**。两个目标本来不冲突，是没分清。

### 修法

`backend/app/migrations.py`：

1. 引入 `_is_benign_alter_error(exc)` helper + `_BENIGN_ALTER_PATTERNS`
   （`duplicate column name` / `already exists`）。用错误消息模式而非
   `sqlite_errorcode`：sqlite_errorcode 对语法错 / 类型不兼容 / 列约束
   不支持都返回 OperationalError，业务侧无法可靠区分。
2. `_apply_one_migration` 内部：benign race → log + return False（不抛）；
   其他真 DDL 错 → 原样 raise。
3. **`run_migrations` 外层 `try/except Exception` 一刀切兜底删掉**——
   内层已经把良性 race 单独识别并吞了，外层再 except 只会反向吞真错。
   良性 IF NOT EXISTS 的 unique-index 路径本来就不抛 duplicate error。

### 修复覆盖测试

`backend/tests/invariants/test_migration_safety.py` 之前 round 1 写的两
个测试**直接 baked in 了错误合同**：

- `test_bad_migration_does_not_crash_run`（原）：模拟坏 DDL，断言不抛 → **改**
- `test_normal_migrations_still_apply_after_bad_one`（原）：monkeypatch
  raise + 断言后续继续 + applied ≥ 1 → **改为断言 raise（fail-fast）**

新合同：

- `test_missing_table_does_not_crash_run`：表不存在走 `_table_exists` 跳过
  路径，不抛（这条还是 idempotent 设计，不变）
- `test_real_ddl_failure_propagates`：monkeypatch `_apply_one_migration`
  raise RuntimeError → 必须 raise 透传，且不再继续后续 migration（外层
  兜底已删）

## 真实问题 2：测试隔离（记录，不深挖）

### 现象

跑 `tests/test_phase1_5_smoke.py` 整套时（6 failed / 7 passed / 1 skipped），
6 个失败全部是：

```
sqlalchemy.exc.IntegrityError: FOREIGN KEY constraint failed
[SQL: INSERT INTO novel_ai_bindings ...]
```

`test_cold_start` / `test_sse_end_to_end` / `test_concurrency_mutex_db` /
`test_dashboard_command` / `test_budget_command` / `test_scan_command`
全部挂在 `novel_ai_bindings.project_id` 找不到对应 `Project` 行——`_seed_project_and_binding`
用 `db.merge(p)` 但 commit 时机不对（Project 行其实没 flush 到 DB，FK
约束炸了）。

但**单独跑这些测试全部 PASSED**。这是 audit 反复强调的 pre-existing
测试隔离问题：

> "test_bridge.py::TestOrphanBridgeRunRecovery / TestBridgeRunConcurrencyGuard
> 单独拎出来跑，全部 PASSED。... 这不是逻辑 bug，是测试间相互污染"

### 决定

按 user 原文 + memory「prioritize real risks over future armor」原则：
- 暂不深挖（与 round 3 修复**无关**——migration fail-fast 改动不引入 FK 问题）
- 记一笔到这里
- 建议未来用 pytest-randomly 之类工具专门抓「什么状态在测试间泄漏」

### 验证（与本次修复直接相关）

3 个 migration 测试 + 5 个 invariants 子模块 + 4 个 round-2 已验证文件：

```
tests/test_phase1_5_smoke.py::test_migration_fail_fast_on_ddl_error PASSED
tests/test_phase1_5_smoke.py::test_migration_skip_missing_table PASSED
tests/test_phase1_5_smoke.py::test_migration_idempotent_on_existing_column PASSED
tests/invariants/test_migration_safety.py ... 3 passed
tests/invariants/test_chapter_uniqueness.py ..... 5 passed
tests/invariants/test_bridge_run_pid.py ... 3 passed
tests/invariants/test_engine_watchdog.py ... 3 passed
tests/invariants/test_strip_junk_removed.py ... 3 passed
tests/test_utils_helpers.py ... 12 passed
tests/test_alignment_smoke.py ... 22 passed
tests/test_alignment_stages.py ... 7 passed
tests/test_cold_memory.py ... 7 passed

小计：65 passed（与 round 2 baseline 一致）
```

未运行整套的原因：phase1_5_smoke 整套跑会撞测试隔离问题（FK 失败，
pre-existing，与本次修复无关）。

## 跳过项

### S1. Provider / RoleAssignment 多用户归属模型
- 来源：用户原文判断
- 原因：是产品决策（这两张表没 owner_id 字段，是不是每用户一份 vs
  全局共享），不是工程问题。等真要上线生产前定。

### S2. 17 test 全量失败（test 隔离）
- 来源：用户原文 + 实际复现
- 原因：pre-existing，与 round 3 修复无关；按 memory「不投生产护栏」
  原则暂不深挖，已记录。

### S3. 自述文档 #8-#17 的「暂不修」项
- 来源：用户原文复核
- 原因：判断合理（HttpOnly cookie 死代码、outline 抽卡已修、provider 无
  鉴权等都是「等真要上线再处理」级别的项），按 `docs/audit/security-fixes.md`
  的计划表顺序处理。

## Commit

`fix(migrations): 异常分类（fail-fast + TOCTOU 并存）`（修完后统一提交）

## 真实问题 1 涉及文件

- `backend/app/migrations.py` — 加 `_is_benign_alter_error` helper + 删外层 except
- `backend/tests/invariants/test_migration_safety.py` — 改两个 baked-in 错合同的测试