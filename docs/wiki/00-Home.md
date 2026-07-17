# novel_AI Code Wiki

结构化项目文档，供人类与 AI Agent 快速理解本仓库的架构、模块职责、关键接口与运行方式。

> 本 wiki 与仓库根目录的 `README.md`（使用指南）、`CHANGELOG.md`（发布级变化）互补，不重复其内容，侧重**架构级理解**。
>
> 修订 2026-07-16：删掉了与 `novel_AI/CLAUDE.md` / `novel_AI/index.md` / `novel_AI/PROJECT_LOG.md` 的引用（这些元文件随 novel_AI/ 独立版仓库一起删除）；同时合并了 `07-Standalone-Engine.md` 的内容到 [03-Writing-Engine.md](03-Writing-Engine.md) 末尾。

## 目录

| 文档 | 内容 |
|------|------|
| [01-Architecture.md](01-Architecture.md) | 项目整体架构、子系统边界、进程拓扑、请求生命周期 |
| [02-Backend-API.md](02-Backend-API.md) | FastAPI 后端：路由清单、认证模型、安全机制、RAG、世界构建向导 |
| [03-Writing-Engine.md](03-Writing-Engine.md) | LangGraph 多 Agent 写作引擎：状态机、9 个 Agent、记忆系统、预算与合规、移植加固历史 |
| [04-Frontend.md](04-Frontend.md) | React + TypeScript 前端：路由、API 客户端、组件、SSE 数据流 |
| [05-Data-Model.md](05-Data-Model.md) | SQLAlchemy 数据模型与实体关系 |
| [06-Dev-Setup.md](06-Dev-Setup.md) | 本地开发环境搭建、启动顺序、常用命令、部署注意事项 |

## 项目一句话简介

**novel_AI** 是一个用多 Agent 协作写长篇网文的工程：FastAPI + React 的 Web 框架内嵌一个 LangGraph 多 Agent 写作引擎。前端点点按钮，9 个写作 Agent（Planner / Writer / Normalizer / Compliance / Checker×3 / Rewriter / Outline / Summarizer / Tracker）协同生成设定、规划章节、逐章写作、质量评审与重写，章节自动入库并支持语义检索。

## 顶层目录速览

```
Novel_AI/
├── backend/            FastAPI 后端（项目管理、世界构建、Provider/角色配置、桥接、章节检索）
│   ├── app/            Web 层：路由、模型、鉴权、RAG、世界构建向导
│   └── engine/         LangGraph 写作引擎（迁移自早期 novel_AI/ 独立版，10 个维度加固）
├── frontend/           React + TypeScript + Vite 前端（写作引擎控制台）
├── docs/               项目文档（结构化 wiki + 审计/实测报告）
└── dev.bat             Windows 一键启停脚本（后端 + 前端）
```

所有业务逻辑都位于 `backend/` 和 `frontend/`；历史独立引擎与融合期临时结构可通过 Git 历史追溯。

**核心关系**：`backend/app`（Web 层）通过 `backend/app/api/bridge.py` 以**独立子进程**方式调用 `backend/engine`（写作引擎），两者以文件系统（`engine` 输出目录下的 JSON/TXT 文件）+ stdout 日志流为通信媒介，而非直接函数调用——这样 uvicorn 重启/热重载不会杀死正在跑的写作任务。`backend/engine` 是从早期独立版仓库（已删除）移植并加固的版本，详见 [03-Writing-Engine.md](03-Writing-Engine.md) 末尾的「移植加固历史」表格。
