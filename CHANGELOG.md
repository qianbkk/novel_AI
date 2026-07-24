# Changelog

This file records release-level behavior changes. Individual fixes and implementation details remain available through `git log`.

## Unreleased

### Added

- **Dashboard 运行时可见性**（`1ecb726`）：`/projects` 端点附带 `active_run_command/status/id/started_at` 字段；Dashboard 项目卡片显示 ⟳+命令名+呼吸动画 badge，bridge.run 期间可见，点跳 BridgeConsole。
- **`backend/scripts/continue_worldbuild.py`**：续跑卡在 stage 2（MiniMax 突发 429 等）的 draft 项目；跑前清掉上一轮失败的中间产物，每 stage 6 次重试。实测 14 分钟 worldbuild 完成。
- **`backend/scripts/drive_30ch_bridge.py`**：按前端 BridgeConsole 按钮 1:1 跑完整 7 步 pipeline（binding → push-concept → planner → pull-setting → bootstrap → init_arc → run N → import-chapters）；全程 HTTP API + SSE，不直接调 `engine.tools`。
- **真实 31 章跑通**（2026-07-24）：real30ch-16862056 项目从 worldbuild 到 import-chapters 完整跑通，31 章、64,545 字、$0.74 总成本。

### Fixed

- **#13 pull-setting 重复人物**：planner 同时把 `protagonist.name` 放进 `key_characters[]` → 同一 name 写 2 行（之前 7 character 含 2 林渊）。`app/bridge/setting_sync.py` `_add_character` 加 `seen_names` 守门。
- **#14 Dashboard 看不到正在跑**：见上"Added"段。

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
