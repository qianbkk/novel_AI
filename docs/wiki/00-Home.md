# novel_AI Code Wiki

结构化项目文档，供人类与 AI Agent 快速理解本仓库的架构、模块职责、关键接口与运行方式。

> 本 wiki 与仓库根目录的 `README.md`（使用指南）、`CHANGELOG.md`（发布级变化）互补，不重复其内容，侧重**架构级理解**。
> 历史设计与审计报告归档于 `docs/runs/_archive/`，不再活跃引用。
>
> **工程化基线**：2026-07-25 完成 P0/P1 全部 9 项短板 + /simplify 4 项高 ROI 修复 + /code-review 2 项 critical bug 修复（13 commits）。当前基线 ~75/100，详见 `CHANGELOG.md` Unreleased 段。

## 阅读顺序

| 顺序 | 文档 | 何时看 |
|------|------|------|
| 1 | [00-Home.md](00-Home.md)（本页） | 项目一句话 + 顶层目录速览 |
| 2 | [ARCHITECTURE.md](ARCHITECTURE.md) | 5 分钟看清三层结构 + 关键不变量（速览） |
| 3 | [01-Architecture.md](01-Architecture.md) | 需要改核心路径时再读完整版 |
| 4 | [02-Backend-API.md](02-Backend-API.md) | 改后端 API / 加路由 |
| 5 | [03-Writing-Engine.md](03-Writing-Engine.md) | 改引擎状态机 / Agent prompt / 记忆 |
| 6 | [04-Frontend.md](04-Frontend.md) | 改前端页面 / 组件 / API client |
| 7 | [05-Data-Model.md](05-Data-Model.md) | 改表 / 加字段 |
| 8 | [06-Dev-Setup.md](06-Dev-Setup.md) | 本地启动 / 部署 / 测试 |
| 9 | [07-Real-LLM-Testing.md](07-Real-LLM-Testing.md) | 真实 LLM 测试前必看（问题清单 + 根因） |
| 10 | [08-Frontend-Runbook.md](08-Frontend-Runbook.md) | 仅通过前端按钮操作的用户视角 |

## 文档维护规则（来自 `docs/INDEX.md`）

- 当前行为写到对应主题文档；不另起新 phase/iteration 报告
- 一份内容只在一处文档维护，其余文档**链过去**而非**抄过去**
- 用现在时写"现状"，用 Git 历史追溯"来龙去脉"
- 临时草稿进 `docs/drafts/`，跑测试产物进 `docs/runs/`（均 `.gitignore`）
- 不在文档里写 commit hash 或手工维护的"最后更新"字段

## 项目一句话简介

**novel_AI** 是一个用多 Agent 协作写长篇网文的工程：FastAPI + React 的 Web 框架内嵌一个 LangGraph 多 Agent 写作引擎。前端点点按钮，9 个写作 Agent（Planner / Writer / Normalizer / Compliance / Checker×3 / Rewriter / Outline / Summarizer / Tracker）协同生成设定、规划章节、逐章写作、质量评审与重写，章节自动入库并支持语义检索。

## 顶层目录速览

```
Novel_AI/
├── backend/            FastAPI 后端 + 写作引擎（同一个 Python 包）
│   ├── app/            Web 层：路由、模型、鉴权、RAG、世界构建向导、桥接
│   └── engine/         LangGraph 写作引擎（被后端以子进程调用）
├── frontend/           React + TypeScript + Vite 前端
├── docs/               项目文档
│   ├── wiki/           当前活跃 wiki（你看的就是这个）
│   └── runs/           跑测试的临时产物（`.gitignore`）
└── dev.bat             Windows 一键启停脚本（后端 + 前端）
```

所有业务逻辑都位于 `backend/` 和 `frontend/`；早期独立版 `novel_AI/` 已并入 `backend/engine/`，迁移与加固历史见 [03-Writing-Engine.md](03-Writing-Engine.md#移植自独立版-novel_ai的关键加固)。

**核心关系**：`backend/app`（Web 层）通过 `backend/app/api/bridge.py` 以**独立子进程**方式调用 `backend/engine`（写作引擎），两者以文件系统（`engine` 输出目录下的 JSON/TXT 文件）+ stdout 日志流为通信媒介，而非直接函数调用——这样 uvicorn 重启/热重载不会杀死正在跑的写作任务。
