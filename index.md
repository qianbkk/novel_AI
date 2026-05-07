# index.md — 项目文件索引

本文件是项目所有文件的完整目录，每条目包含路径、类型、一句话描述和主要依赖关系。不包含使用说明或状态信息，相关内容见 `README.md` 和 `PROJECT_LOG.md`。

**维护规则**：新增文件时在对应分区追加一行；删除文件时划去或移除对应行；修改文件核心职责时更新描述列。

---

## 入口与配置

| 路径 | 类型 | 描述 | 主要依赖 |
|------|------|------|----------|
| `run.py` | 入口脚本 | 统一命令行入口，路由所有用户命令 | `orchestrator.py`、所有 `tools/` |
| `orchestrator.py` | 核心调度 | LangGraph 7节点状态机，协调全部 Agent 的执行顺序与重试逻辑 | `langgraph`、所有 `agents/`、`memory/` |
| `orchestrator_state.py` | 数据结构 | Orchestrator 全局状态 TypedDict Schema 及持久化方法 | 无 |
| `api_client.py` | 基础设施 | 多模型 API 路由层，含 Prompt Cache、Token 预算、调用统计 | `anthropic`、`httpx` |
| `.env.template` | 配置模板 | 所有 Provider 的 API Key 配置模板，复制为 `.env` 后填写 | 无 |

---

## 元文件

| 路径 | 类型 | 描述 | 主要依赖 |
|------|------|------|----------|
| `CLAUDE.md` | 行为约束 | Claude Code 行为规则、元文件更新约束、调试规范 | 无（最高优先级文件） |
| `README.md` | 使用指南 | 项目架构、安装步骤、命令参考、模型配置、成本预估 | 无 |
| `index.md`（本文件） | 文件索引 | 所有文件的路径、描述与依赖关系目录 | 无 |
| `PROJECT_LOG.md` | 状态日志 | 阶段进度、关键决策、成本追踪、待办事项 | 无 |
| `.claude/lessons.md` | 经验积累 | 已发生的错误、根因分析、修复方式及规律 | 无 |

---

## 配置文件

| 路径 | 类型 | 描述 | 主要依赖 |
|------|------|------|----------|
| `config/novel_config.json` | 项目配置 | 小说基础信息、目标字数、预算上限、默认模型配置 | 无 |
| `config/compliance_rules_fanqie.json` | 合规规则 | 番茄小说平台的硬拒绝规则、警告规则及章节字数要求 | 无 |
| `config/prompt_templates.py` | 提示词库 | 七种章末钩子定义、爽点类型、三题材专属写作指令、通用写作铁律 | 无 |

---

## Agents（专项执行单元）

| 路径 | 类型 | 描述 | 主要依赖 |
|------|------|------|----------|
| `agents/planner_agent.py` | Agent | 根据世界观概念生成完整设定包（书名/人物/力量体系/弧规划） | `api_client` |
| `agents/outline_agent.py` | Agent | 将弧级大纲拆解为带钩子类型和爽点要求的章节任务单 | `api_client`、`config/prompt_templates` |
| `agents/writer_agent.py` | Agent | 生成章节正文，集成按需记忆检索、Prompt Cache 和风格样本 | `api_client`、`memory/memory_manager`、`config/prompt_templates` |
| `agents/normalizer_agent.py` | Agent | 三道防线去 AI 腔：词汇替换 + LLM 改写 + 格式检查 | `api_client` |
| `agents/compliance_agent.py` | Agent | 番茄平台合规检查，关键词快速扫描 + LLM 语义审核 | `api_client`、`config/compliance_rules_fanqie.json` |
| `agents/checker_agent.py` | Agent | 五维度加权质检评分（钩子/爽感/人物/逻辑/文笔），支持三模型交叉 | `api_client` |
| `agents/rewriter_agent.py` | Agent | P0/P1/P2 三级修订，P0 含自检清单（时间线/因果/状态五项） | `api_client` |
| `agents/tracker_agent.py` | Agent | 章节完成后更新 L2 记忆（热层人物状态/剧情线/约束过期） | `api_client`、`memory/memory_manager` |
| `agents/summarizer_agent.py` | Agent | 弧结束时生成 L5 长程摘要，压缩章节历史到冷层 | `api_client`、`memory/memory_manager` |

---

## 记忆系统

| 路径 | 类型 | 描述 | 主要依赖 |
|------|------|------|----------|
| `memory/memory_manager.py` | 记忆管理 | L2 热冷分离读写、按需检索、约束自动过期、L3 风格样本动态切换、L5 读写 | 无 |
| `memory/l2/<novel_id>_memory.json` | 运行时数据 | L2 实时叙事状态（人物/剧情线/约束/伏笔），由 Tracker 写入 | 无 |
| `memory/l5/<novel_id>_l5.json` | 运行时数据 | L5 长程压缩记忆（弧摘要/重大揭示），由 Summarizer 写入 | 无 |

---

## 工具集

| 路径 | 类型 | 描述 | 主要依赖 |
|------|------|------|----------|
| `tools/bootstrap.py` | 工具 | 黄金三章多版本生成（A/B/C），输出风格锚点，选定后初始化 Tracker | `agents/writer_agent`、`agents/checker_agent` |
| `tools/human_review.py` | 工具 | 交互式人工审核界面，处理三类 pending 任务 | `orchestrator_state` |
| `tools/dashboard.py` | 工具 | 文字版质量看板（热力图/趋势/维度均分/记忆状态） | `memory/memory_manager` |
| `tools/exporter.py` | 工具 | 章节汇编导出为番茄格式 TXT，支持全量/按弧/按范围 | 无 |
| `tools/budget_manager.py` | 工具 | 成本记录（JSONL）、预算预警、投影分析、按 Agent 分组 | `orchestrator_state` |
| `tools/chapter_checker.py` | 工具 | 跨章节一致性扫描（点数逻辑/境界合法性/设定矛盾） | `api_client`、`memory/memory_manager` |
| `tools/fingerprint_checker.py` | 工具 | 文风指纹统计检测（句长方差/段首多样性/AI词汇密度）+ 角色口癖执行检测 | `api_client`、`config/prompt_templates` |
| `tools/style_manager.py` | 工具 | 管理外部/内部风格样本库，生成 Writer 风格提示词前缀 | 无 |
| `tools/calibrate_checker.py` | 工具 | Checker 基线校准，用内置或外部样本验证三模型评分一致率 | `api_client` |
| `tools/acceptance_tests.py` | 工具 | 五大验收标准 AC-1~5（设定一致性/题材切换/任务单/平台适配/弧光） | `memory/memory_manager`、`config/prompt_templates` |
| `tools/system_test.py` | 测试 | 20 项集成测试（含 Mock LLM），全通过代表系统完整就绪 | 全部模块 |

---

## 输出目录

| 路径 | 类型 | 描述 | 写入方 |
|------|------|------|--------|
| `output/setting_package.json` | 生成物 | Planner 输出的完整设定包 | `planner_agent` |
| `output/orchestrator_state.json` | 运行时数据 | Orchestrator 全局状态持久化文件，每章完成后自动保存 | `orchestrator` |
| `output/arc_<N>_tasks.json` | 生成物 | 第 N 弧的章节任务单（Outline Agent 输出） | `outline_agent` |
| `output/chapters/ch_<N>.txt` | 生成物 | 第 N 章最终正文 | `orchestrator` |
| `output/chapters/ch_<N>_meta.json` | 元数据 | 第 N 章的质检分数、重写次数、字数等元数据 | `orchestrator` |
| `output/bootstrap_candidates.json` | 生成物 | 黄金三章各版本得分对比摘要（不含全文） | `bootstrap` |
| `output/exports/` | 导出物 | 汇编导出的 TXT 稿件 | `exporter` |
| `output/reports/` | 报告 | 一致性扫描报告、预算报告、校准结果 | 各工具 |
| `logs/budget_log.jsonl` | 日志 | 每次 API 调用的成本记录（追加写） | `budget_manager` |

---

## 其他

| 路径 | 类型 | 描述 |
|------|------|------|
| `style_samples/` | 样本库 | 风格参考文本（`ext_` 前缀=外部，`int_` 前缀=内部高分自动提取，`anchor_` 前缀=黄金三章锚点） |
| `calibration/` | 校准数据 | Checker 基线校准用的人工标注样本（格式见 `calibration/README.md`） |
| `agents/__init__.py` | 包标识 | 使 agents 目录可作为 Python 包导入 |
| `memory/__init__.py` | 包标识 | 使 memory 目录可作为 Python 包导入 |
| `tools/__init__.py` | 包标识 | 使 tools 目录可作为 Python 包导入 |
