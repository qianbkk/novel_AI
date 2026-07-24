# Changelog

This file records release-level behavior changes. Individual fixes and implementation details remain available through `git log`.

## Unreleased

### 工程化提升（2026-07-24 → 2026-07-25）

本阶段把 63/100 的工程化基线推到 75/100。10 个 commit 覆盖 P0 全部 6 项 + P1 全部 3 项 + 1 项 /simplify 高 ROI 修复 + 2 项 /code-review critical bug 修复。

#### P0 — 必修（6 项全部完成）

| 短板 | 修复 | Commit |
|------|------|--------|
| 404 兜底 + ErrorBoundary 缺失 | NotFound.tsx + ErrorBoundary.tsx class 组件，路由 `<ErrorBoundary key={pathname}>` 包 `<Routes>`，404 path="*" 兜底 | `656b2be` |
| API client 缺 abort/timeout/retry | `RequestOptions { signal/timeoutMs/retries }` + `withTimeout` 5 行 + retry 退避 + `withConcurrency` sliding window + `useSafeAsync` hook | `ad9d8f2` |
| 无 CI / lint / pre-commit | GitHub Actions 双 job（backend pytest + frontend lint/typecheck/build）+ ruff 0.6.9 (devDep) + pre-commit hooks | `5eb3033` |
| BridgeRun TOCTOU 并发漏洞 | partial unique index `WHERE status IN ('pending','running')` 数据库层硬保证 + pre-cleanup 归档孤儿 + 3 个新测试 | `3b80b3c` |
| app↔engine 双向 import | 抽 `shared/atomic_io.py` + `shared/setting_schema.py`，原模块 re-export shim 兼容（消除 4/8 双向 import） | `ccdae15` |
| 核心链路裸 dict | 11 个 Pydantic v2 模型镜像 setting_package.json 字段，setting_sync 入口软验证 | `d5be2d6` |

#### P1 — 应当改（3 项全部完成）

| 短板 | 修复 | Commit |
|------|------|--------|
| 4 个巨型 page 组件 | WorldBuild.tsx 1030 → 751 行：抽 EmptyTab / FactionGraph / WorldviewTab + groupMapByLevel helper | `6283e2b` |
| 212 处 inline style | Dashboard `chipStyle()` JS 函数 → `.genre-chip` CSS class + 6 个 dashboard class，13 处收编 | `3a1afda` |
| FK nullable + ondelete 缺失 | 16 个 FK 加 ondelete（CASCADE/SET NULL）+ Project.owner_id FK + alembic 0003 + 5 个测试 | `e6e1a1d` |

#### 审计优化（/simplify + /code-review）

| 修复 | Commit |
|------|--------|
| /simplify 4 高 ROI：Dashboard 接入 withConcurrency + NotFound/ErrorBoundary copyDebugInfo 合并 + 删 retryOnPost YAGNI + groupMapByLevel inline | `3bb98c4` |
| /code-review 2 critical bug：useSafeAsync signal 永远不被 abort + migrations ROW_NUMBER NULL ordering | `（本次 commit）` |

#### 未做（评估后 skip）

- **alembic 0003 部署验证未跑**：migration 写好但 alembic upgrade head 实际未在生产 DB 执行（FK 约束名匹配、列数对齐、UNIQUE 重复行清理在生产环境需手动验证）。
- **useSafeAsync 落地 10 个 page**：hook 自身 bug 已修，但 10 个 page 仍手写 `mountedRef`，落地工作量大。
- **_build_minimal_card 噪声数据**：60 行把 key_characters 套 8 段留空 "待补全"，需同步改前端 CharacterCard fallback 渲染。
- **Pydantic + JSON Schema 双契约**：184 行 Pydantic 镜像 JSON Schema，需 codegen 单向绑定，工作量大。
- **ErrorBoundary 用 react-error-boundary 库**：167 行 class 改库调用，UI 是产品设计资产，搬过去成本与收益不匹配。
- **frontend retry 删 + 改 Idempotency-Key**：破坏性大。

### Added

- **Dashboard 运行时可见性**（`1ecb726`）：`/projects` 端点附带 `active_run_command/status/id/started_at` 字段；Dashboard 项目卡片显示 ⟳+命令名+呼吸动画 badge，bridge.run 期间可见，点跳 BridgeConsole。
- **`backend/scripts/continue_worldbuild.py`**：续跑卡在 stage 2（MiniMax 突发 429 等）的 draft 项目；跑前清掉上一轮失败的中间产物，每 stage 6 次重试。实测 14 分钟 worldbuild 完成。
- **`backend/scripts/drive_30ch_bridge.py`**：按前端 BridgeConsole 按钮 1:1 跑完整 7 步 pipeline；全程 HTTP API + SSE，不直接调 `engine.tools`。
- **`backend/scripts/backfill_worldbuild.py`**：幂等回灌已有项目（real30ch-16862056 实测 6 char / 5 factions / 4 relations）。
- **真实 31 章跑通**（2026-07-24）：real30ch-16862056 项目从 worldbuild 到 import-chapters 完整跑通，31 章、64,545 字、$0.74 总成本。
- **CI 质量门**（`5eb3033`）：PR/push 自动跑 backend pytest + frontend lint/typecheck/build + ruff（仅改文件）。
- **404 兜底路由**（`656b2be`）：`<Route path="*" element={<NotFound />} />`。
- **路由级 ErrorBoundary**（`656b2be`）：子组件渲染抛错降级到友好错误页 + 复制调试按钮。
- **API client abort/timeout/retry**（`ad9d8f2`）：GET 默认 30s 超时 + 1 次重试，POST 默认 60s 无重试。
- **withConcurrency 滑动窗口**（`ad9d8f2`）：替代 Dashboard 4 并发手写 chunk 循环。
- **useSafeAsync hook**（`ad9d8f2`）：统一 mountedRef + AbortController 模式。
- **BridgeRun partial unique index**（`3b80b3c`）：DB 层硬保证"一个 project 至多一条 active run"。
- **shared/atomic_io**（`ccdae15`）：跨 app↔engine 原子写 helper（解决双向 import）。
- **shared/setting_schema**（`ccdae15`）：跨 app↔engine JSON Schema 校验（解决双向 import）。
- **Pydantic 核心模型**（`d5be2d6`）：11 个 SettingPackage / WorldviewRich / CharacterCard / EntityRelationRich。
- **Genre chip CSS class**（`3a1afda`）：13 处 inline 收编为 .genre-chip + .genre-chip--active。
- **alembic 0003 fk_cascade_unique**（`e6e1a1d`）：16 FK ondelete CASCADE/SET NULL + Project.owner_id FK + ChapterCharacter/Outline UNIQUE。
- **CopyDebugButton 共用**（`3bb98c4`）：NotFound + ErrorBoundary 16 行复制逻辑合并为 1 处。

### Fixed

- **#13 pull-setting 重复人物**：planner 同时把 `protagonist.name` 放进 `key_characters[]` → 同一 name 写 2 行（之前 7 character 含 2 林渊）。`app/bridge/setting_sync.py` `_add_character` 加 `seen_names` 守门。
- **#14 Dashboard 看不到正在跑**：见上"Added"段。
- **P0-1 章节阅读器独立滚动**：`.reader-page` 锁 viewport 高度 + TOC 与正文各自 overflow-y 独立滚动条。
- **P1-3 FK nullable + ondelete**：14 个 project_id FK 加 CASCADE、MapNode.parent_id 加 CASCADE、Foreshadowing.linked_character_id 加 SET NULL、Project.owner_id 加 FK(users.id) nullable。
- **/code-review useSafeAsync signal 泄漏**（本次）：hook 之前在 `controllerRef.current?.signal ?? new AbortController().signal` 每次 render 都 fallback 一个新 controller，cleanup abort 不到。改用 useState 惰性初始化保证 ref 稳定。
- **/code-review migrations NULL ordering**（本次）：`_run_pre_index_cleanups` ROW_NUMBER OVER ORDER BY started_at DESC 在 SQLite 下两条 NULL 行保谁任意。加 `COALESCE(started_at, '1970-01-01') + id DESC` 兜底。

### Documentation

- `docs/wiki/07-Real-LLM-Testing.md` 增补 2026-07-24 实战经验（§8 续跑 draft 项目 + §9 Bridge pipeline 实测数据 + §10 待办）。
- `docs/wiki/00-Home.md` 重写为"阅读顺序 + 维护规则"格式，移除指向已删除 `07-Standalone-Engine.md` 的死链。
- `docs/wiki/03-Writing-Engine.md` 移除对已删除文档的引用。
- `docs/wiki/06-Dev-Setup.md` 新增 `continue_worldbuild.py` / `drive_30ch_bridge.py` 章节。
- `docs/wiki/08-Frontend-Runbook.md` §3.7 新增 Dashboard ⟳ badge 故障排查章节。
- `docs/wiki/ARCHITECTURE.md` 重新定位为"5 分钟速览"（不再是与 00/01 重复的速览）。
- `docs/audit/`、`docs/superpowers/` 空目录清理；历史报告归档至 `docs/runs/_archive/`。

## 2026-07-22


### Added

- Real LLM end-to-end testing experience documented at `docs/wiki/07-Real-LLM-Testing.md`, capturing the 8 issues found during a 30-chapter MiniMax-M3 run and the root-cause fixes that prevent regression.
- Chapter title parsing covers the LLM "semi-legal JSON" case (body contains real newlines) so titles stop coming through as `{"title": ..., "body": ...}` literals in the database, frontend, and per-chapter meta files.
- 30 chapter outputs include a 4-arc plan in the world-build result and project creation/modification timestamps on dashboard cards.

### Changed

- `engine/llm/router.py` retries MiniMax HTTP 529 with 6 attempts and 2-60s exponential backoff and prints each attempt so the bridge no longer appears to hang for up to two minutes.
- `engine/agents/writer.py` extracts a title even when the model wraps the response in JSON, removing the silent fallback that surfaced JSON snippets as chapter titles.
- `engine/orchestrator.save_chapter` re-runs the title extractor before persisting so `ch_NNNN.txt` is always plain body, never the raw LLM response.
- `app/worldbuild/stages.py` deduplicates characters within a single stage by name, removing duplicate "林渊" rows introduced when the mock payload re-emits the protagonist.
- `app/security.py` logs master-key source, length, and sha256 fingerprint on startup so key drift across restarts is immediately visible.
- `engine/workers/run_bridge_subprocess.py` loads `backend/.env` on startup when the parent process did not pass it through, preventing `MINIMAX_API_KEY 未设置` failures in subprocess runs.
- `scripts/preset_worldbuild.py` runs the 10-stage worldbuild against the real LLM by default (was hard-coded mock), populating the eight character-card fields.
- `frontend/src/pages/ChapterReader.tsx` rejects any title that is JSON-shaped or longer than 30 characters, showing "（标题待生成）" instead of leaking the malformed data.

### Fixed

- Frontend project list now shows creation and modification timestamps.
- Bootstrap stage no longer leaves a stale `BridgeRun` in `running` state when stdout is quiet during HTTP 529 retries.
- Stage `characters` no longer writes a duplicate row when the same name appears twice in the LLM output.

### Documentation

- Consolidated active documentation around the architecture Wiki and removed completed audit, benchmark, and phase-plan reports from the working tree.
- Removed duplicate pytest collection through the legacy invariant re-export module and documented the canonical test layout.
- Classified supported maintenance scripts and removed one-off benchmark and migration drivers.
- Archived the older `docs/runs/30ch-real-2026-07-20/` run under `docs/runs/_archive/`; the 2026-07-22 run remains the canonical real-LLM example.

## 2026-07

### Added

- Long-form continuity support: structured worldbuilding snapshots, foreshadowing operations, cross-arc memory inheritance, and final-chapter handling.
- A zero-cost chapter rule checker feeding the six-dimension LLM quality review.
- Multi-user authentication hardening, project ownership isolation, login rate limiting, and production startup validation.
- Atomic persistence, backup/restore support, corruption recovery, and cross-storage reconciliation tools.

### Changed

- Unified the standalone writing engine into `backend/engine` and made the web bridge its supported execution path.
- Split structural invariant tests by domain and made test path discovery independent of the current working directory.
- Standardized the backend development port on `8132` and the frontend development port on `5293`.

For earlier detail, use `git log --all -- CHANGELOG.md` or inspect the relevant source file history.
