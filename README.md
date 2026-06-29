# novel_AI

novel_AI：一个用多 Agent 协作写长篇网文的工程。

FastAPI + React Web 框架内嵌一个 LangGraph 多 Agent 写作引擎。前端点点按钮，9 个写作 Agent（Planner / Writer / Normalizer / Compliance / Checker×3 / Rewriter / Outline / Summarizer / Tracker）协同生成设定、规划章节、逐章写作、质量评审与重写，章节自动入库。

仓库目录：

- `backend/`：FastAPI 后端，提供项目管理、世界构建、Provider/角色配置、写作引擎桥接、章节导入与检索。
- `frontend/`：React + TypeScript + Vite 前端，提供项目页面、Provider 管理、角色配置和写作引擎控制台。
- `docs/`：项目文档与可视化页面（含自解释指南 `novel_ai_fusion_guide.html`）。
- `patches/`：写作引擎的修复 apply 指南，跨机器需手动 apply。

## 本地运行

后端：

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8123
```

前端：

```bash
cd frontend
npm install
npm run dev
```

写作引擎依赖（NovelAI 项目根目录）：

```bash
cd novel_AI
pip install langgraph anthropic httpx jieba
```

打开 `http://localhost:5293`。

## 使用顺序

1. 在 Provider 页面配置模型供应商。
2. 在角色配置页面为 15 个写作角色绑定 Provider 和模型。
3. 新建项目并完成世界构建。
4. 在写作引擎控制台绑定 `novel_AI` 目录。
5. 推送设定、生成设定包、运行章节写作，并导入章节。

## 一键 MVP 脚本（CLI）

替代手动点 4 个按钮，用 `backend/scripts/run_mvp.py` 顺序跑：push-concept → planner → pull-setting → bootstrap → select → run N → import-chapters。

```bash
cd backend
# 1. 启动后端（另一个终端）
uvicorn app.main:app --reload --port 8123

# 2. 在 frontend 新建项目 + 完成 worldbuild（10 阶段），记下 project_id
# 3. 跑 MVP（默认写 1 章，选版本 A）
python -m scripts.run_mvp <project_id>
# 或：python -m scripts.run_mvp <project_id> --chapters 3 --select B
```

流式打印 SSE 日志 + node 事件，结束给摘要 + 列落盘章节。

## novel_AI Bug 修复

`novel_AI/` 在 `.gitignore`，跨机器需手动 apply。详见 `patches/2026-06-28-novel_ai-mvp-fixes.md`（修复 HTTP 连接池、node_rewrite 漏 compliance、预算阈值、网络重试）。

## 注意

`.env`、运行日志、数据库、构建产物和缓存不会提交到仓库。