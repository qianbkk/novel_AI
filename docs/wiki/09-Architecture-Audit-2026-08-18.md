# 09 · 架构审查：6 个真实问题背后的设计模式（2026-08-18）

> 本文档记录 2026-08-18 一次完整架构审查的方法论、发现与修复。
> 适用场景：未来遇到同类问题（操作前不可知 / 状态不可见 / 一次性黑盒 / 旅程断裂）时回来对照根因，避免重新踩坑。

## 1. 触发：用户报了 6 个看起来分散的问题

| 用户报的现象 | 表面看是 | 实际是 |
|---|---|---|
| dev.bat 启动显示有问题 | 编码 bug | **平台编码基础设施不一致** |
| Dashboard 按钮与项目名重叠 + 筛选漏洞 | UI bug | **状态管理与素材池依赖 current view** |
| 创建小说后开始构建直接失败 | 后端 bug | **重资源操作前无预检** |
| 生成大纲失败 | 后端 bug | 同上 + **错误链路不穿透** |
| WorldBuild 看不到东西 | 前端 bug | **后端状态全程不可见** |
| 整个前端布局不合理 | UI bug | **前端是功能清单而不是用户旅程** |

每一条都不是简单的字面 bug。每个背后都是一个**架构模式**：
- **操作前不可知**：重资源操作（调 LLM / 启动子进程）之前没有任何方式确认前置条件是否就绪
- **状态不可见**：后端 / 引擎 / SSE 在不在跑，前端只能从请求失败反推
- **一次性黑盒**：所有"生成"操作都是「点了就等结果」，没有版本化、没有 diff、没有"重抽 N 个候选选一个"
- **用户旅程断裂**：每个页面是独立工具，进入页面不知道「我在哪、下一步是什么、为什么」
- **平台/编码/路径不一致**：Windows / POSIX / chcp / BOM / PYTHONIOENCODING 多处不统一

## 2. 修复原则（不是字面 bug 修补，是架构方案）

1. **预检前置**：任何调 LLM 的操作之前必须探测 LLM 是否就绪，不可用时给引导
2. **状态可视化**：后端是否在跑 / LLM 是否就绪 / 当前操作跑到第几步 — 必须让前端随时知道
3. **错误链路穿透**：失败必须留下具体根因 + 修复路径，不静默吞
4. **用户旅程连贯**：每个页面顶部显示当前所在步骤 + 下一步建议
5. **平台一致**：编码、路径、进程间通信跨 Windows/POSIX 必须一致

## 3. 改动清单

### 3.1 后端新增

| 路径 | 作用 |
|------|------|
| `backend/app/api/providers.py` GET `/providers/health` | LLM 全局健康检查 + 3 角色路由明细 + DB provider 列表 |
| `backend/app/api/providers.py` POST `/providers/{id}/test` | 单 provider 联通测试 + 上游错误透传 |
| `backend/tests/test_provider_health_2026_08_18.py` | 6 个测试覆盖 mock/live/4xx/404 |

### 3.2 前端新增

| 路径 | 作用 |
|------|------|
| `frontend/src/components/LLMStatusBanner.tsx` | 折叠/展开 LLM 状态 banner，集成"去配置"快捷按钮 |
| `frontend/src/hooks/useBackendHealth.ts` | 每 5s 探测 `/health` 的三态 hook |
| `frontend/src/pages/App.tsx` `BackendStatusBadge` | sidebar 实时健康灯 + 错误详情展开 |

### 3.3 前端改动

| 路径 | 改动 |
|------|------|
| `frontend/src/pages/Dashboard.tsx` | 项目卡嵌入 WritingJourney stepper；点击跳"当前步骤"对应页；新增 availableGenres 题材池；批量 toolbar 改进；搜索框清除按钮 + Esc 清多选 |
| `frontend/src/pages/WorldBuild.tsx` | 顶部加 LLMStatusBanner；开始构建按钮 LLM 不可用时禁用 |
| `frontend/src/pages/Outline.tsx` | 顶部加 LLMStatusBanner；handleGenerate 操作前预检 |
| `frontend/src/pages/ThemeOpening.tsx` | 顶部加 LLMStatusBanner |
| `frontend/src/pages/NewProject.tsx` | 顶部加 LLMStatusBanner + 5 步旅程说明；创建后跳 `/theme`（Pre-Production） |
| `frontend/src/pages/BridgeConsole.tsx` | runBridge 操作前预检 LLM 状态 |
| `frontend/src/types.ts` | ProviderHealth / ProviderTestResult 类型 |
| `frontend/src/api/client.ts` | getProviderHealth / testProvider 方法 |
| `frontend/src/styles.css` | `.project-card__title` padding-left；`.writing-journey` stepper |

### 3.4 基础设施改动

| 路径 | 改动 |
|------|------|
| `dev.bat` | launcher 加 `set PYTHONIOENCODING=utf-8` + `set PYTHONUNBUFFERED=1` |
| `backend/engine/agents/{genre,opening,macro_spine,theme,research}_profiler/designer.py` 等 5 个 `load_*` | `except Exception: return None` 加 `_log.exception`，符合 CLAUDE.md「失败要响亮」（之前跟 normalizer 修复 cd57dfd 同模式） |
| 同上 5 个文件 + `scene_quality_check.py` 共 6 个 `_parse_llm_json` wrapper | 删除并改 caller 直调 `utils.parse_llm_json_response`（utils 已 log + 失败返 default） |
| `backend/engine/memory/manager.py`、`backend/engine/graph.py`、`backend/engine/tools/human_review.py` | 3 个 backup-rename fallback `except: pass` → `log.exception`（数据丢失风险点） |
| `backend/engine/tools/budget_manager.py` | 3 处 `except: pass` → `_log.warning` / `_log.exception` |

## 4. 同模式未来排查清单

未来再出现类似"用户反馈某个功能不顺"时，按这个清单排查：

- [ ] **重资源操作前是否预检？**（调 LLM / 启动子进程 / 写大文件之前）
- [ ] **状态是否全程可见？**（后端 / 引擎 / SSE 状态在前端有 banner / badge / 进度条？）
- [ ] **失败链路是否穿透？**（用户能看到具体根因 + 修复路径，而非 "调用 LLM 失败"？）
- [ ] **用户旅程是否连贯？**（每个页面顶部有"我在哪 + 下一步是什么"？）
- [ ] **平台/编码/路径是否一致？**（Windows / POSIX / chcp / BOM / PYTHONIOENCODING）
- [ ] **是否一次性黑盒？**（生成操作是否支持版本化 / 多候选 / 重抽？）
- [ ] **是否依赖 current view？**（筛选 / 资源池是否独立于当前显示？）
- [ ] **是否有 silent fail？**（except Exception / except BaseException 是否吞了真实错误？）

## 5. 不在本次范围内的发现（留给后续任务）

审计期间发现但本次不动的问题（避免 CLAUDE.md「修复一个凑合一个」的反模式）：

- **章节生成不支持多候选 + 版本化**：点击"写章节"直接出 1 章，没有"再抽 3 个选一个"的迭代工作流。这是 v1.0 决策（"前期工程深度"路线），不是 bug，但用户报"大纲需要多轮探讨"已经触及此点 → 留作后续版本
- **chapters 列表没有 LLMStatusBanner**：本章是查询 + 编辑页面，不调 LLM，但写入 chapter 时会触发；目前没暴露预检入口 → 后续接入
- **run_draft / 试错模式未做预检**：和 run 一样的 LLM 依赖但用户跑 draft 时通常不希望硬卡 → 留开关
- **Providers 页面没有内嵌 test 按钮**：`POST /providers/{id}/test` 端点已就绪但前端未接入 → 1 小时工作

这些项**不阻塞本次架构修复**，原因：用户明确说"不要吹毛求疵"。

## 6. 验证

```bash
# 后端测试
pytest backend/tests/test_provider_health_2026_08_18.py -v
# 6 passed

# 前端编译
npm --prefix frontend run build
# 62 modules, 360KB JS / 84KB CSS, build ok

# 完整基线（需要时跑）
pytest backend/tests --ignore=backend/tests/invariants
pytest backend/tests/invariants
```

## 7. 升级迁移

- 无 DB schema 变更
- 无新增环境变量
- 无新增依赖
- 旧项目无感升级：进入页面自动显示新 banner；项目卡自动显示 stepper