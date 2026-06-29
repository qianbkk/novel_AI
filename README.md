# Novel AI Fusion Assistant

融合版 AI 小说写作助手。

本仓库把两个原型项目合并成一个可运行项目：

- `backend/`：FastAPI 后端，提供项目管理、世界构建、Provider/角色配置、novel_AI 桥接、章节导入与检索。
- `frontend/`：React + TypeScript + Vite 前端，提供项目页面、Provider 管理、角色配置和写作引擎控制台。
- `novel_AI/`：LangGraph 多 Agent 写作引擎源码，作为后端 bridge 的子进程执行目标。
- `novel-assistant/`：原 novel-assistant 参考项目，保留用于对照。

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

novel_AI 依赖：

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
