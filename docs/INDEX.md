# Documentation

This directory documents the current system. Historical implementation plans, completed audits, and benchmark transcripts belong in Git history rather than the active documentation tree.

## Start here

- [Wiki home](wiki/00-Home.md): system overview and reading order.
- [Architecture](wiki/01-Architecture.md): process boundaries and request lifecycle.
- [Backend API](wiki/02-Backend-API.md): FastAPI routes and contracts.
- [Writing engine](wiki/03-Writing-Engine.md): orchestration, agents, memory, and quality gates.
- [Frontend](wiki/04-Frontend.md): React application structure and data flow.
- [Data model](wiki/05-Data-Model.md): persistence and entity relationships.
- [Development](wiki/06-Dev-Setup.md): local setup, scripts, tests, and deployment.
- [Real LLM testing](wiki/07-Real-LLM-Testing.md): 30-chapter end-to-end run experience, root causes, and runbook.
- [Frontend runbook](wiki/08-Frontend-Runbook.md): user-facing operation flow + troubleshooting (no manual backend).
- [Architecture audit 2026-08-18](wiki/09-Architecture-Audit-2026-08-18.md): 6 个真实问题背后的设计模式 + 修复清单 + 同模式未来排查清单。
- [Architecture quick reference](wiki/ARCHITECTURE.md): concise operational view and invariants.

## Maintenance policy

- Update the owning document when behavior changes; do not create a new phase or iteration report.
- Keep one source of truth per subject. Link to it instead of copying sections between files.
- Describe the current state in present tense. Use Git history for previous designs and completed investigations.
- Put temporary drafts under `docs/drafts/` and benchmark output under `docs/runs/`; both are ignored.
- Do not record commit hashes or manually maintained “last updated” fields in documentation.
- Remove stale instructions in the same commit that removes or renames the referenced code.

The root [README](../README.md) remains the product and setup entry point. The root [CHANGELOG](../CHANGELOG.md) records only release-level changes, not every commit.

## 文档分工表

每份文档都有明确的所有权主题，避免内容漂移：

| 文档 | 所有权 | 不写什么 |
|------|--------|----------|
| `README.md` | 产品/设置/部署入口 | 不写架构细节、API 列表、prompt 模板 |
| `CHANGELOG.md` | 发布级行为变化 | 不写单个 commit 的细节、不写修复过程 |
| `docs/INDEX.md` | 文档索引 + 维护规则 | 不写文档内容，只链过去 |
| `docs/wiki/00-Home.md` | 阅读顺序 + 项目一句话 | 不写模块细节 |
| `docs/wiki/ARCHITECTURE.md` | 5 分钟架构速览 | 不写路由清单、agent 实现细节 |
| `docs/wiki/01-Architecture.md` | 三层拓扑 + 失败模式 | 不写 API 路径 |
| `docs/wiki/02-Backend-API.md` | 路由清单 + 契约 | 不写 agent 实现、不写引擎细节 |
| `docs/wiki/03-Writing-Engine.md` | 9-Agent orchestrator + 记忆 | 不写后端路由、不写前端组件 |
| `docs/wiki/04-Frontend.md` | 前端结构 + 数据流 | 不写后端细节 |
| `docs/wiki/05-Data-Model.md` | ORM 表 + 跨存储契约 | 不写业务逻辑 |
| `docs/wiki/06-Dev-Setup.md` | 启动/部署/测试命令 | 不写架构解释 |
| `docs/wiki/07-Real-LLM-Testing.md` | 30 章真实跑批 + 根因清单 | 不写开发命令 |
| `docs/wiki/08-Frontend-Runbook.md` | 用户视角操作流程 | 不写后端实现 |
| `docs/wiki/09-Architecture-Audit-2026-08-18.md` | 架构审查产物 + 排查清单 | 不写日常维护 |
| `docs/drafts/*` | 临时设计草稿（已 gitignore） | 不进活跃文档 |
| `docs/runs/*` | 本地真实 LLM 跑批产物（已 gitignore） | 不作为文档依赖 |
