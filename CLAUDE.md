# CLAUDE.md

本文件是仓库级长期约束。开始任何任务前先读 `README.md`、`docs/INDEX.md` 和本文件；具体架构以 `docs/wiki/` 与源码为准。

## 项目边界

- 后端是 FastAPI + SQLAlchemy，前端是 React + TypeScript + Vite。
- `backend/engine` 是 LangGraph 长篇写作引擎，通过独立子进程运行。
- 后端与引擎以绑定目录中的 JSON/TXT 文件显式同步，不要擅自改成进程内调用。
- 复用现有 Schema、Provider 路由、角色分配、预算、质量门、分层记忆和 BridgeRun；不要建立平行系统。

## 必须保持的不变量

- 所有 project-scoped API 必须执行 ownership 校验；生产模式不得退化为匿名访问。
- Provider key、JWT、cookie、Authorization、完整 prompt 和模型原始敏感响应不得写入日志、SSE 或错误响应。
- 所有 LLM 调用必须走现有路由并计入预算；禁止绕过质量门或静默吞掉调用失败。
- 引擎落盘应使用现有原子写入模式；重复执行、进程中断和恢复不得覆盖已完成章节或重复回写状态。
- 旧数据库、旧章节 meta 和旧设定包保持向后兼容。数据库结构变化必须有 Alembic/迁移方案和回归测试。
- 不复制许可证不兼容的第三方代码。外部项目只借鉴思想，代码复用前先核对许可证。

## 修改规则

- 先调查真实调用链，再写能复现问题的测试，再做最小实现。
- 保留工作区已有改动；不要回滚、覆盖或格式化任务范围外的文件。
- 未经任务明确授权，不增加依赖、环境变量、数据库表、公共 API，不修改核心 Agent prompt 或 LangGraph 拓扑。
- 不以通过测试为目的删除、跳过、放宽断言或扩大 mock。
- 不创建 phase/iteration/audit 报告。当前行为更新到已有主题文档，历史过程留在 Git。
- 临时输出放在已忽略的 `docs/runs/`、`docs/drafts/` 或系统临时目录，不提交运行产物和真实用户数据。
- Windows 是主要开发环境；路径处理同时考虑 Windows 和 POSIX，文件读写显式使用 UTF-8。

## 验证

行为测试和结构不变量测试必须使用两个独立 pytest 进程：

```powershell
pytest backend/tests --ignore=backend/tests/invariants
pytest backend/tests/invariants
python -m compileall -q backend/app backend/engine backend/scripts backend/tests
npm --prefix frontend run build
git diff --check
git status --short
```

可以先运行目标测试，但交付时必须报告实际运行的命令、结果、未验证项和剩余风险。不得声称未实际运行的检查已经通过。

## Git

- 禁止 `git reset --hard`、`git checkout --`、强制推送和破坏性清理。
- 一个任务对应一个聚焦提交；提交前检查 `git diff --stat` 和 `git diff --check`。
- 除非用户明确要求，不 push、不合并到 `main`、不改写历史。
