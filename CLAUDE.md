# CLAUDE.md

仓库级长期约束。只放系统级规则；具体架构与操作细节以 `README.md`、`docs/INDEX.md`、`docs/wiki/` 和源码为准，开工前先读。

## 项目边界

- 后端 FastAPI + SQLAlchemy，前端 React + TypeScript + Vite。
- `backend/engine` 是 LangGraph 长篇写作引擎，以独立子进程运行，与后端只通过绑定目录里的 JSON/TXT 文件同步 —— 不要改成进程内调用。
- 复用现有 Schema、Provider 路由、角色分配、预算、质量门、分层记忆和 BridgeRun，不建平行系统。

## 不变量

- project-scoped API 必须做 ownership 校验；生产模式不得退化为匿名访问。
- Provider key、JWT、cookie、Authorization、完整 prompt 和模型原始敏感响应不得进日志、SSE 或错误响应。
- 所有 LLM 调用走现有路由并计入预算；不得绕过质量门或静默吞掉调用失败。**失败要响亮**：故障转移、降级、占位兜底都必须留下明确信号。
- prompt 里不得出现任何具体项目的专名（角色名/地名/世界名）—— 一律从 setting 渲染，缺失时降级为中性措辞。
- 引擎落盘用现有原子写入模式；重复执行、进程中断和恢复不得覆盖已完成章节或重复回写状态。
- 旧数据库、旧章节 meta、旧设定包保持向后兼容；表结构变化必须有迁移方案和回归测试。
- 不复制许可证不兼容的第三方代码。

## 修改规则

- 先查真实调用链 → 再写能复现问题的测试 → 再做最小实现。
- 保留工作区已有改动；不回滚、覆盖或格式化任务范围外的文件。
- 不以通过测试为目的删除、跳过、放宽断言或扩大 mock。**发现断言锁定的是缺陷时，改成断言正确行为并写明原因**，不要默默删掉。
- 不增加依赖、环境变量、数据库表或公共 API，除非任务明确要求。
- 不创建 phase/iteration/audit 报告；当前行为更新到已有主题文档，过程留在 Git。临时产物放已忽略的 `docs/runs/`、`docs/drafts/`。
- Windows 是主要开发环境；路径同时兼容 Windows 和 POSIX，文件读写显式 UTF-8。

## 验证

行为测试与结构不变量测试必须分两个 pytest 进程：

```powershell
pytest backend/tests --ignore=backend/tests/invariants
pytest backend/tests/invariants
python -m compileall -q backend/app backend/engine backend/scripts backend/tests
npm --prefix frontend run build
git diff --check
```

可以先跑目标测试，但交付时必须报告实际运行的命令、结果、未验证项和剩余风险，并与改动前的基线对照——不得把既有失败说成新回归，也不得声称未实际运行的检查已通过。

跑真实 LLM 端到端前先读 `docs/wiki/07-Real-LLM-Testing.md`（成本、限流、master_key 漂移、env 透传等踩坑均已沉淀在那里）。

## Git

- 禁止 `git reset --hard`、`git checkout --`、强制推送和破坏性清理。
- 一个任务一个聚焦提交；提交前看 `git diff --stat` 和 `git diff --check`。
- 除非用户明确要求，不 push、不合并、不改写历史。
