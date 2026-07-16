# Novel-AI 文档索引

本目录所有文档的统一入口。按用途分组，每组一个子目录。

> **维护原则**：每份文档在「最后更新」列标注 commit hash，避免文档腐烂。

## 一、项目架构与指南

| 文档 | 路径 | 用途 | 最后更新 |
|------|------|------|----------|
| 用户手册 | [`novel-ai-guide.html`](novel-ai-guide.html) | 给最终用户的操作指南 | commit `7310743` |
| Wiki 首页 | [`wiki/00-Home.md`](wiki/00-Home.md) | Wiki 入口，链接所有子章节 | commit `b426b34` |
| 架构总览 | [`wiki/01-Architecture.md`](wiki/01-Architecture.md) | 三层拓扑（frontend/backend/engine） | commit `7310743` |
| 后端 API | [`wiki/02-Backend-API.md`](wiki/02-Backend-API.md) | FastAPI 路由 + 端点 | commit `7310743` |
| 写作引擎 | [`wiki/03-Writing-Engine.md`](wiki/03-Writing-Engine.md) | orchestrator + 9 个 agent + 移植加固历史 | commit `99707c3` |
| 前端 | [`wiki/04-Frontend.md`](wiki/04-Frontend.md) | React + Vite 组件 | commit `7310743` |
| 数据模型 | [`wiki/05-Data-Model.md`](wiki/05-Data-Model.md) | SQLAlchemy ORM + 4 套存储 | commit `7310743` |
| 开发环境 | [`wiki/06-Dev-Setup.md`](wiki/06-Dev-Setup.md) | dev.bat + npm run dev | commit `d9daabc` |
| 架构现状速览 | [`wiki/ARCHITECTURE.md`](wiki/ARCHITECTURE.md) | 速览入口 + CHANGELOG 链接 | commit `4d3b7c9` |

## 二、审计追踪（按 commit 时序）

### 总入口

| 文档 | 路径 | 用途 | 最后更新 |
|------|------|------|----------|
| **Master Audit Tracker** | [`master-audit-tracker.md`](master-audit-tracker.md) | **所有审计工作的统一索引**：A-L 组 + commit 列表 + M 段 300ch 验证 | commit `aa7c347` |

### 详细报告（按审计轮次）

| 文档 | 路径 | 范围 | 关键 commit |
|------|------|------|-------------|
| 安全修复 5 项 | [`audit/security-fixes.md`](audit/security-fixes.md) | Chapter 唯一约束 / BridgeRun pid / watchdog / TOCTOU / strip-junk | `52492ce` |
| Code Review Action | [`audit/code-review-action.md`](audit/code-review-action.md) | linshi.txt 两份审查报告辩证应用（4 项） | `90dbf62` |
| Simplify Round 2 | [`audit/simplify-round2.md`](audit/simplify-round2.md) | 4 cleanup agents, 13 应用 / 7 跳过 | `0fcf0bc` |
| Simplify Round 3 | [`audit/simplify-round3.md`](audit/simplify-round3.md) | fail-fast + TOCTOU 并存（异常分类） | `4be81b2` |

### 根因分析历史

| 文档 | 路径 | 用途 | 最后更新 |
|------|------|------|----------|
| Root Cause Analysis | [`root-cause-analysis.md`](root-cause-analysis.md) | 50 章端到端暴露的 5 个真实 bug | commit `0613a3d` |

## 三、大规模验证记录（runs）

| 文档 | 路径 | 规模 | 关键发现 |
|------|------|------|----------|
| 300 章 v3 实测报告 | [`runs/300ch-v3.md`](runs/300ch-v3.md) | 300 ch / $15 / 6h | P1-P7 修复在大规模下 0 orchestrator error |
| 300 章 v3 深度复盘 | [`runs/300ch-retrospective.md`](runs/300ch-retrospective.md) | 反思 + 优化建议 | Critical: concept 漂移 + ch_0300 整章[待修订] |

## 四、历史目录（已完成 phase 的归档文档，不更新）

| 目录 | 用途 | 状态 |
|------|------|------|
| [`superpowers/_archive_plans/`](superpowers/_archive_plans/) | Phase 1 / 1.5 / 3 / 4 规划文档（已完成） | 历史归档，不更新 |
| [`superpowers/_archive_specs/`](superpowers/_archive_specs/) | Phase 1.5 / Phase 4 设计 spec（已完成） | 历史归档，不更新 |

> 这些文档对应的工程 phase 已合并到代码。新读者**不需要读**，写在这里避免 git log 检索漏掉 + 给历史审计留索引。

---

## 阅读顺序建议

### 给新人看
1. [`novel-ai-guide.html`](novel-ai-guide.html) — 用户视角
2. [`wiki/00-Home.md`](wiki/00-Home.md) — 架构全景
3. [`wiki/ARCHITECTURE.md`](wiki/ARCHITECTURE.md) — 现状速览
4. [`master-audit-tracker.md`](master-audit-tracker.md) — 看历史审计

### 给工程师看
1. [`master-audit-tracker.md`](master-audit-tracker.md) — 全部 commit 索引
2. [`runs/300ch-retrospective.md`](runs/300ch-retrospective.md) — 当前已知问题 + 下一步
3. [`audit/`](audit/) — 按轮次看审计细节

### 给产品 / PM 看
1. [`runs/300ch-v3.md`](runs/300ch-v3.md) — 实测统计
2. [`runs/300ch-retrospective.md`](runs/300ch-retrospective.md) — 反思 + concept 漂移警告
3. [`wiki/01-Architecture.md`](wiki/01-Architecture.md) — 三层拓扑

---

**最后更新**: commit `aa7c347` (2026-07-16)
**维护人**: Claude Code AI (qianbkk)