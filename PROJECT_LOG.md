# PROJECT_LOG.md — 项目状态日志

本文件记录项目的阶段进度、关键决策和成本情况。只追加，不修改历史条目。架构说明见 `README.md`，文件描述见 `index.md`。

---

## 阶段进度

| 阶段 | 名称 | 状态 | 完成时间 | 备注 |
|------|------|------|----------|------|
| Phase 1 | 环境搭建 | ✅ 完成 | 2026-05-05 | Python 3.12，langgraph/anthropic/jieba 就绪 |
| Phase 2 | 设定包生成 | ✅ 完成 | 2026-05-05 | setting_package.json 已生成，含4弧157章规划 |
| Phase 3 | 黄金三章 Bootstrap | ⏳ 待执行 | — | 需填写 API Key 后运行 `python run.py bootstrap` |
| Phase 4 | 状态追踪系统 | ✅ 完成 | 2026-05-05 | L2热冷分离+约束过期+按需检索已实现，系统测试通过 |
| Phase 5 | Orchestrator全量上线 | ✅ 完成 | 2026-05-05 | LangGraph 7节点，含预算硬停和指纹检测集成 |
| Phase 6 | Token优化+修订系统 | ✅ 完成 | 2026-05-05 | Prompt Cache、P0自检清单、三级修订全部就绪 |
| Phase 7 | 全速生产 | 🔒 未开始 | — | 等待黄金三章选定后启动 |

---

## 人工介入节点状态

| 节点 | 类型 | 状态 | 说明 |
|------|------|------|------|
| 节点① | 设定包确认 | ⏳ 待确认 | 设定包已生成，用户尚未通过 `run.py review` 正式确认 |
| 节点② | 弧任务单审核 | 🔒 未触发 | 将在每弧开始前自动触发 |
| 节点③ | 问题章节处理 | 🔒 未触发 | 将在章节重写3次不达标时触发 |

---

## 关键决策记录

| 时间 | 决策 | 原因 |
|------|------|------|
| 2026-05-05 | 确认世界观：人情债古老契约体系 | 用户提供，差异化明显，差于常见赘婿/重生套路 |
| 2026-05-05 | 主角设定：成长流，非完全新手，能力被压制后重新觉醒 | 用户指定，兼顾成长弧与开局底气感 |
| 2026-05-05 | 平台：番茄小说 | 用户指定，已按番茄合规规则配置 |
| 2026-05-05 | 书名候选首选：《债线纵横》 | Planner 生成，待用户最终确认 |
| 2026-05-05 | 默认写作模型：Claude Sonnet（Writer/Rewriter），DeepSeek（Checker/Tracker） | 质量与成本平衡 |
| 2026-05-05 | 新增模型支持：MiniMax、自定义 Provider | 用户需求，已在 api_client.py 实现 |

---

## 系统测试记录

| 时间 | 测试版本 | 结果 | 备注 |
|------|----------|------|------|
| 2026-05-05 | V1（17项） | 17/17 通过 | 初始版本 |
| 2026-05-05 | V2（20项） | 20/20 通过 | 新增memory_manager/fingerprint/acceptance测试 |
| 2026-05-08 | V3（代码质量修复） | 语法检查通过 | 含simplify两轮修复 |

---

## 代码质量修复记录（Simplify）

| 日期 | 修复内容 | 影响 |
|------|----------|------|
| 2026-05-08 | 提取 shared utilities: parse_llm_json_response / config/power_levels / config/paths | 消除6处JSON解析重复、2处POWER_LEVELS重复 |
| 2026-05-08 | 修复tracker_agent死代码（run_tracker提前return） | L2记忆持久化首次生效 |
| 2026-05-08 | 添加utils/__init__.py缺失的`import os` | load_env()不再崩溃 |
| 2026-05-08 | memory_manager硬编码"感债者"改用DEFAULT_POWER_LEVEL | 统一默认值 |
| 2026-05-08 | orchestrator._setting()添加模块级缓存 | 消除每章3次JSON重复解析 |
| 2026-05-08 | api_client._get_client()连接池 | 消除每API调用新建TCP/TLS握手 |

---

## 成本追踪

| 阶段 | 消耗 (USD) | 累计 (USD) | 备注 |
|------|-----------|-----------|------|
| 系统构建（无实际写作调用） | $0.00 | $0.00 | Mock 测试，未调用真实 API |

**预算配置**：上限 $500.00 ｜ 95% 硬停（$475） ｜ 80% 预警（$400）

---

## 待办事项

- [ ] 用户填写 `.env` 文件（ANTHROPIC_API_KEY + DEEPSEEK_API_KEY）
- [ ] 运行 `python run.py bootstrap` 生成黄金三章候选版本
- [ ] 用户通过 `python run.py review` 确认设定包（节点①）
- [ ] 阅读黄金三章 A/B/C 版本，执行 `python tools/bootstrap.py select N X` 选定版本
- [ ] 运行 `python run.py run 10` 开始首批正式生产
- [ ] （可选）运行 `python run.py calibrate` 校准 Checker 基线
