# 独立版写作引擎（`novel_AI/`）

`novel_AI/` 是写作引擎的**独立 CLI 参考实现**——不在 git 版本控制内（跨机器需手动 apply `patches/` 中的补丁），有自己完整的元文件体系（`CLAUDE.md`/`README.md`/`index.md`/`PROJECT_LOG.md`/`.claude/lessons.md`）。`backend/engine/` 是从这里移植并加固过的版本，供后端子进程调用（见 [03-Writing-Engine.md](03-Writing-Engine.md)）。

## 架构（与 `backend/engine` 共享）

```
用户 (CLI 指令)
      ↓
  run.py  ← 统一命令入口
      ↓
Orchestrator (LangGraph 状态机)
      ↓ 路由调度
Planner → Outline → Writer → Normalizer → Compliance → Checker×3
                                                    ↓
                              通过 → Tracker (记忆更新)
                              不通过 → Rewriter (P0/P1/P2)
      ↓ 弧结束
  Summarizer (L5 长程记忆)
```

记忆分层：L2 热冷分离（热层近 20 章 / 冷层压缩历史）+ L5 弧级摘要，Writer 每章仅注入约 1500 token 按需上下文。

## 独立运行方式

```bash
pip install langgraph anthropic httpx jieba
cp .env.template .env
# 编辑 .env，至少填写 ANTHROPIC_API_KEY、DEEPSEEK_API_KEY

python run.py planner      # 生成设定包（需人工审阅）
python run.py bootstrap    # 黄金三章 A/B/C 候选
python tools/bootstrap.py select 1 A   # 选定版本
python run.py run 10       # 正式写作 10 章
```

### 命令参考

| 类别 | 命令 |
|------|------|
| 写作流程 | `planner` / `bootstrap` / `run [N]` / `resume` / `init_arc` |
| 监控 | `status` / `dashboard` / `show [N]` / `pending` / `memory` |
| 人工审核 | `review` |
| 导出 | `export` / `export arc N` / `stats` |
| 质量维护 | `test` / `calibrate` / `fingerprint` / `ac [all\|ac1-5]` / `scan` / `budget` / `style list` |

### 人工介入的三个节点

1. Planner 生成设定包后，需人工确认世界观/人物/弧规划（`python run.py review`）
2. 每个新弧开始前，任务单生成后提醒审阅（recommended 级，可跳过）
3. 某章重写超过 3 次仍不达标，标记 `[待修订]` 并暂停，等待人工选择（接受/强制重写/手动编辑/跳过）

### 模型路由（`api_client.py` 的 `MODEL_ROUTES`）

| Agent | Provider | 理由 |
|-------|----------|------|
| Writer / Rewriter / Planner | Claude Sonnet | 创作质量优先 |
| Orchestrator / Outline / Compliance / Tracker | DeepSeek | 成本优先 |
| Checker 主评 | DeepSeek | 成本优先 |
| Checker 交叉 1 | Claude Sonnet | 交叉校验 |
| Summarizer | Claude Sonnet | 长文压缩 |

支持 Provider：`anthropic` / `deepseek` / `gemini` / `kimi` / `minimax` / `custom`。

### 成本预估

| 场景 | 每章成本 |
|------|----------|
| 全流程（3 模型质检） | ~$0.04（Prompt Cache 命中后更低） |
| 精简模式（单模型质检，`audit_mode: lite`） | ~$0.02 |
| 100 章 | ~$4 |
| 300 万字（约 1500 章） | ~$60 |

预算上限在 `config/novel_config.json` 的 `budget_limit_usd` 设置，达到 95% 自动暂停。

## 目录结构（摘自 `novel_AI/index.md`）

| 目录 | 内容 |
|------|------|
| `agents/` | 9 个 Agent（planner/outline/writer/normalizer/compliance/checker/rewriter/tracker/summarizer） |
| `memory/` | `memory_manager.py` + `l2/<novel_id>_memory.json` + `l5/<novel_id>_l5.json` |
| `tools/` | bootstrap/human_review/dashboard/exporter/budget_manager/chapter_checker/fingerprint_checker/style_manager/calibrate_checker/acceptance_tests/system_test |
| `config/` | `novel_config.json`、`compliance_rules_fanqie.json`、`prompt_templates.py`、`power_levels.py`、`paths.py` |
| `output/` | `setting_package.json`、`orchestrator_state.json`、`arc_<N>_tasks.json`、`chapters/`、`bootstrap_candidates.json`、`exports/`、`reports/` |
| `calibration/` | Checker 基线校准的人工标注样本 |

## 与 `backend/engine` 的差异

`backend/engine` 不是精简子集，而是**忠实的架构移植版**——绝大多数文件头部注释都写着 "Migrated from novel_AI/..."。关键差异：

- **去模块级全局状态**：`novel_AI/api_client.py` 用模块级全局变量；`backend/engine/llm/router.py` 的 `LLMRouter` 改为按实例，支持多项目/多线程互不干扰的后端场景。
- **新增 DB 驱动配置层**：`backend/engine/llm_router.py`（独立版没有）从 `Provider`/`RoleAssignment` 表读取加密 API Key 和代理配置，注入引擎的 `LLMRouter`；独立版无数据库，纯靠环境变量。
- **新增子进程隔离入口**：`workers/run_bridge_subprocess.py`（独立版没有对应物）——独立版 `run.py` 是普通 CLI 入口，backend 需要与 uvicorn 生命周期隔离。
- **新增 Mock Provider**：`llm/router.py` 的 `_mock()`（含完整的按 Agent 分类的 mock 响应）用于后端 CI/测试免费跑通全流程，独立版没有。
- **大量加固层**：数十处以"迭代 #NN"编号的注释记录具体 bug 修复（原子 JSON 写入、fail-loud 替代假通过、去重感知的记忆合并、`human_escalation` 路由死路修复、成本重复计算修复）——这些是移植**之后**针对真实生产事故追加的加固（例如 `ch_0064` 假通过 bug、"50 章 0 character 边"导入顺序 bug）。`novel_AI/` 是更早期的原始参考实现。
- **backend 独有的缺失项**：`dashboard` 命令尚未移植（`graph.py:456` 显式标注 "not yet ported (P3)"）；`card`/`talk` 大纲模式和 `run_draft`/`set_audit_mode` 命令是 backend 独有的新增能力，独立版 `run.py` 中没有。
- **状态机拓扑一致但独立版有历史 bug**：两边都是功能等价的 6 节点图，但 `backend/engine/orchestrator.py` 中有明确代码注释（约 738-744 行）记录独立版原始拓扑是 `human_escalation → END`（导致运行提前终止的 bug），backend 版已修复为循环回 `load_arc_tasks`。

## 元文件体系（`novel_AI/` 特有的自解释约定）

`novel_AI/` 维护五个职责严格分离的元文件（`CLAUDE.md` 中定义）：

| 文件 | 职责 |
|------|------|
| `CLAUDE.md` | Claude Code 行为规则、元文件更新约束、调试规范 |
| `README.md` | 项目架构、安装步骤、命令参考、模型配置、成本预估 |
| `index.md` | 所有文件的路径、类型、一句话描述、依赖关系 |
| `PROJECT_LOG.md` | 阶段进度、关键决策、成本记录、待办事项 |
| `.claude/lessons.md` | 已踩过的错误、根因分析、修复方式、规律总结 |

这套约定只作用于 `novel_AI/` 目录本身，`backend/engine/` 及仓库其余部分不遵循这套元文件体系（改用普通 git 提交历史 + 本 wiki）。
