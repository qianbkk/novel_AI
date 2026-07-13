# novel_AI Code Wiki

结构化项目文档，供人类与 AI Agent 快速理解本仓库的架构、模块职责、关键接口与运行方式。

> 本 wiki 与仓库根目录的 `README.md`（使用指南）、`novel_AI/CLAUDE.md`（行为约束）、`novel_AI/index.md`（文件索引）、`novel_AI/PROJECT_LOG.md`（进度日志）互补，不重复其内容，侧重**架构级理解**。

## 目录

| 文档 | 内容 |
|------|------|
| [01-Architecture.md](01-Architecture.md) | 项目整体架构、子系统边界、进程拓扑、请求生命周期 |
| [02-Backend-API.md](02-Backend-API.md) | FastAPI 后端：路由清单、认证模型、安全机制、RAG、世界构建向导 |
| [03-Writing-Engine.md](03-Writing-Engine.md) | LangGraph 多 Agent 写作引擎：状态机、9 个 Agent、记忆系统、预算与合规 |
| [04-Frontend.md](04-Frontend.md) | React + TypeScript 前端：路由、API 客户端、组件、SSE 数据流 |
| [05-Data-Model.md](05-Data-Model.md) | SQLAlchemy 数据模型与实体关系 |
| [06-Dev-Setup.md](06-Dev-Setup.md) | 本地开发环境搭建、启动顺序、常用命令、部署注意事项 |
| [07-Standalone-Engine.md](07-Standalone-Engine.md) | `novel_AI/` 独立版写作引擎（CLI 参考实现）及与 `backend/engine` 的差异 |

## 项目一句话简介

**novel_AI** 是一个用多 Agent 协作写长篇网文的工程：FastAPI + React 的 Web 框架内嵌一个 LangGraph 多 Agent 写作引擎。前端点点按钮，9 个写作 Agent（Planner / Writer / Normalizer / Compliance / Checker×3 / Rewriter / Outline / Summarizer / Tracker）协同生成设定、规划章节、逐章写作、质量评审与重写，章节自动入库并支持语义检索。

## 顶层目录速览

```
Novel_AI/
├── backend/            FastAPI 后端（项目管理、世界构建、Provider/角色配置、桥接、章节检索）
│   ├── app/            Web 层：路由、模型、鉴权、RAG、世界构建向导
│   └── engine/          LangGraph 写作引擎的"内嵌副本"（供 backend 以子进程方式调用）
├── frontend/            React + TypeScript + Vite 前端（写作引擎控制台）
├── novel_AI/            LangGraph 写作引擎的独立参考实现（CLI 工具，未纳入 git，需手动同步）
├── novel-assistant/     早期原型（世界构建 Web UI），已并入 backend/frontend，仅作历史参考
├── docs/                项目文档、实施计划（superpowers/plans、specs）
├── patches/             novel_AI 跨机器同步的补丁说明
└── dev.bat              Windows 一键启停脚本（后端 + 前端）
```

**核心关系**：`backend/app`（Web 层）通过 `backend/app/api/bridge.py` 以**独立子进程**方式调用 `backend/engine`（写作引擎），两者以文件系统（`novel_ai_dir` 下的 JSON/TXT 文件）+ stdout 日志流为通信媒介，而非直接函数调用——这样 uvicorn 重启/热重载不会杀死正在跑的写作任务。`backend/engine` 是从 `novel_AI/`（独立 CLI 版本）移植并加固过的版本，详见 [07-Standalone-Engine.md](07-Standalone-Engine.md)。
