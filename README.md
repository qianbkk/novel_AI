# AI网文创作系统 V3

基于 LangGraph + Orchestrator 主 Agent 架构的全自动网络小说创作系统，目标产出三百万字长篇网文，适配番茄小说平台。

---

## 架构概览

```
用户 (自然语言指令)
      ↓
  run.py  ← 统一命令入口
      ↓
Orchestrator (LangGraph 状态机，7节点)
      ↓  路由调度
┌─────────────────────────────────────────┐
│  Planner → Outline → Writer             │
│       ↓         ↓        ↓             │
│  设定包    任务单    正文草稿             │
│                          ↓             │
│              Normalizer (去AI腔)        │
│                          ↓             │
│              Compliance (合规检查)      │
│                          ↓             │
│              Checker ×3 (质检评分)      │
│                          ↓             │
│          通过 ──────→ Tracker (记忆更新) │
│          不通过 → Rewriter (P0/P1/P2)   │
└─────────────────────────────────────────┘
      ↓  弧结束
  Summarizer (L5 长程记忆)
```

**记忆层级**：L2 热冷分离（热层近20章 / 冷层压缩历史）+ L5 弧级摘要。Writer 每章只注入约 1500 token 的按需上下文，而非完整记忆。

---

## 安装与配置

**环境要求**：Python 3.10+、pip

```bash
pip install langgraph anthropic httpx jieba
```

**配置 API Key**：

```bash
cp .env.template .env
# 编辑 .env，至少填写：
# ANTHROPIC_API_KEY=sk-ant-xxxxx
# DEEPSEEK_API_KEY=sk-xxxxx
```

其余模型（Gemini、Kimi、MiniMax、自定义）按需填写，详见 `.env.template`。

---

## 启动流程

首次使用按以下顺序执行：

```bash
# 1. 生成设定包（Planner Agent，需人工审阅后确认）
python run.py planner

# 2. 黄金三章：每章生成 A/B/C 三个候选版本
python run.py bootstrap
# 选定版本后执行（以第1章版本A为例）：
python tools/bootstrap.py select 1 A

# 3. 正式生产（每次写10章，可调整数量）
python run.py run 10
```

---

## 命令参考

**写作流程**

| 命令 | 说明 |
|------|------|
| `python run.py planner` | 生成/重新生成设定包 |
| `python run.py bootstrap` | 黄金三章多版本生成 |
| `python run.py run [N]` | 写 N 章（默认10） |
| `python run.py resume` | 从中断点继续 |
| `python run.py init_arc` | 仅生成当前弧任务单 |

**监控与查看**

| 命令 | 说明 |
|------|------|
| `python run.py status` | 进度概览 |
| `python run.py dashboard` | 质量看板（热力图+维度分析） |
| `python run.py show [N]` | 查看第 N 章正文及质检元数据 |
| `python run.py pending` | 待人工处理的任务列表 |
| `python run.py memory` | 记忆系统健康检查 |

**人工审核**

| 命令 | 说明 |
|------|------|
| `python run.py review` | 交互式审核界面（处理 pending 任务） |

**导出**

| 命令 | 说明 |
|------|------|
| `python run.py export` | 导出全部章节为 TXT |
| `python run.py export arc 1` | 导出第1弧 |
| `python run.py stats` | 字数与质量统计 |

**质量与维护**

| 命令 | 说明 |
|------|------|
| `python run.py test` | 运行20项集成测试 |
| `python run.py calibrate` | 校准 Checker 基线 |
| `python run.py fingerprint` | 扫描全部章节的 AI 嫌疑分 |
| `python run.py ac [all\|ac1-5]` | 运行五大验收标准测试 |
| `python run.py scan` | 跨章节一致性扫描 |
| `python run.py budget` | 预算报告 |
| `python run.py style list` | 查看风格样本库 |

---

## 人工介入节点

系统在以下三种情况自动暂停，等待人工处理：

**节点①**：Planner 生成设定包后，需人工确认世界观、人物、弧规划。运行 `python run.py review` 进入交互界面。

**节点②**：每个新弧开始前，任务单生成完毕后提醒审阅（recommended 级，可跳过继续自动运行）。

**节点③**：某章重写超过3次仍不达标，系统标记为 `[待修订]` 并暂停，等待人工选择处理方式（接受/强制重写/手动编辑/跳过）。

---

## 模型配置

默认路由（可在 `api_client.py` 的 `MODEL_ROUTES` 中修改）：

| Agent | Provider | 说明 |
|-------|----------|------|
| Writer / Rewriter / Planner | Claude Sonnet | 创作质量优先 |
| Orchestrator / Outline / Compliance / Tracker | DeepSeek | 成本优先 |
| Checker 主评 | DeepSeek | 成本优先 |
| Checker 交叉1 | Claude Sonnet | 交叉校验 |
| Summarizer | Claude Sonnet | 长文压缩 |

支持的 Provider：`anthropic` / `deepseek` / `gemini` / `kimi` / `minimax` / `custom`

---

## 成本预估

| 场景 | 每章成本 | 备注 |
|------|----------|------|
| 全流程（3模型质检） | ~$0.04 | Prompt Cache 命中后更低 |
| 精简模式（单模型质检） | ~$0.02 | `audit_mode: lite` |
| 100章 | ~$4 | |
| 300万字（1500章） | ~$60 | |

预算上限在 `config/novel_config.json` 的 `budget_limit_usd` 字段设置，达到95%时系统自动暂停。
