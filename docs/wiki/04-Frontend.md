# Frontend（`frontend/`）

React 18 + TypeScript 5 + Vite 5，无外部状态管理库、无 UI 组件库——纯手写 CSS + 原生 `<dialog>` + 原生 SVG 可视化。

## 技术栈与构建

- **Vite**（`vite.config.ts`）：dev server 固定 `5293` 端口，**不做后端代理**——前端通过绝对 URL 直连后端：`API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8132"`（`frontend/src/api/client.ts`），可用 `.env.local` 覆盖。
- **路由**：`react-router-dom` 6，`BrowserRouter`。
- **状态**：纯 `useState`/`useEffect`/`useMemo`；仅一个全局 Context（`ToastProvider`，全局 toast 通知）。
- **脚本**：`npm run dev`（vite）、`npm run build`（`tsc -b && vite build`）、`npm run preview`。
- 无测试框架、无数据请求库（无 React Query/SWR/Redux）——全部手写 `fetch` 封装。

## 路由表（`frontend/src/App.tsx`）

| 路径 | 组件 | 说明 |
|------|------|------|
| `/` | `Dashboard` | 项目列表/搜索/类型筛选、"六大模块"罗盘可视化 |
| `/new` | `NewProject` | 新建项目向导（类型、套路、受众、篇幅、主冲突） |
| `/settings/providers` | `Providers` | LLM Provider CRUD（API Key、base URL、代理开关） |
| `/settings/roles` | `RoleAssignments` | 15 个写作角色绑定 Provider + 可选模型覆盖 |
| `/projects/:projectId/worldbuild` | `WorldBuild` | 10 阶段世界构建向导（SSE 进度）+ 结果多标签页 |
| `/projects/:projectId/chapters` | `Chapters` | 手动录入章节、语义搜索、已存章节列表 |
| `/projects/:projectId/bridge` | `BridgeConsole` | 写作引擎控制台：命令按钮、SSE 日志流、预算/待审面板、目录绑定 |
| `/projects/:projectId/rules` | `RuleCenter` | 风格预设、禁忌词、提示词模板、后处理工具（logic/venom/deai） |
| `/projects/:projectId/characters/:characterId` | `CharacterCard` | 角色 8 部分结构化卡片详情 + 关系列表 |

顶层布局（`App.tsx`）：固定侧边栏全局导航 + 登录状态指示（JWT 存 localStorage，挂载时 `api.meOrNull()` 校验，监听 `window` 事件 `novel_ai:auth_required`）+ 匹配 `/projects/:id/*` 时显示的项目内子导航（worldbuild/bridge/chapters/rules）+ 全局 `LoginDialog`。

## API 客户端层（`frontend/src/api/client.ts`）

单一 `request<T>()` helper 封装 `fetch`：自动注入 `Content-Type` + `Authorization: Bearer <jwt>`（localStorage `novel_ai_jwt`），401 时自动清 token 并派发 `novel_ai:auth_required` 事件，非 OK/非 JSON 响应抛出可读错误。所有函数聚合为 `api` 对象，按后端路由一一对应（详见 [02-Backend-API.md](02-Backend-API.md)）：项目、世界构建、章节、Provider/角色分配、桥接（写作引擎控制台核心）、规则/后处理、伏笔、AI 辅助等级、鉴权。

> 注：`getAiAssistLevel`/`putAiAssistLevel` 已在客户端定义但目前没有任何页面调用——是预留/未完成的功能入口。

## SSE 数据流（无独立 hook，直接内联 `EventSource`）

仓库里没有把 SSE 逻辑抽成 hook，两处长任务页面各自内联：

- **`WorldBuild.tsx`**：`new EventSource(api.worldbuildStreamUrl(...))`，监听 `stage_start`/`stage_done`/`job_done`/`job_failed` 驱动阶段进度 UI。
- **`BridgeConsole.tsx`**：`new EventSource(api.bridgeStreamUrl(...))`，监听更丰富的事件集合（`log`/`start`/`auto_pull_setting_start/done`/`auto_import_chapters_start/done`/`auto_chain_error`/`node_start`/`node_end`/`complete`/`done`/`error`），驱动滚动日志流（按 info/ok/warn/err 着色）、当前节点状态、预算/待审面板。

唯一的自定义 hook是 `frontend/src/hooks/useReveal.ts`：基于 `IntersectionObserver` 的滚动淡入效果，用于 `Dashboard`/`Chapters`/`BridgeConsole`/`RuleCenter`。

## 组件清单（按功能域）

| 领域 | 文件 | 说明 |
|------|------|------|
| 跨页面通用 | `components/Dialog.tsx` | 原生 `<dialog>` 封装（ESC 关闭、遮罩、焦点陷阱） |
| 跨页面通用 | `components/Toast.tsx` | `ToastProvider` + `useToast()`，全局提示条 |
| 跨页面通用 | `components/LoginDialog.tsx` | 登录/注册模态框 |
| 世界构建 | `components/RelationGraph.tsx` | 纯 SVG（无 d3）人物关系图，主角居中，按角色分区环绕，关系类型着色边 |
| 世界构建 | `pages/WorldBuild.tsx` 内联组件 | `WorldviewTab`/`WorldviewSection`/`FactionGraph`（力量体系 SVG 力导向布局） |
| 项目管理 | `pages/Dashboard.tsx` | 项目列表/搜索/筛选 + `ModuleCompass` 装饰性罗盘 |
| 项目管理 | `pages/NewProject.tsx` | 新建项目向导 |
| Provider 管理 | `pages/Providers.tsx` | CRUD 表单 + 列表，MASTER_KEY 警告横幅 |
| 角色分配 | `pages/RoleAssignments.tsx` | 15 角色 → Provider/模型下拉表 |
| 章节管理 | `pages/Chapters.tsx` | 录入表单、语义搜索、"连续性锁定"详情列表 |
| 写作引擎控制台 | `pages/BridgeConsole.tsx` | 最复杂页面：命令按钮、SSE 日志、预算表、人工审核面板（accept/reject/edit）、目录绑定表单、七要素情节轮盘、节点状态翻卡、记忆层"温度计"仪表、章节时间线火花图 |
| 规则中心 | `pages/RuleCenter.tsx` | 风格预设、禁忌词（带音效的原生 dialog 添加流程）、模板试跑、后处理工具卡片 |
| 角色详情 | `pages/CharacterCard.tsx` | 8 部分结构化卡片 + 关系列表，含内联 `SectionCard` 辅助组件 |

## 数据流模式

1. **挂载时拉取**：页面 `useEffect`（依赖 `projectId`）调用 `api.xxx()`，结果存本地 `useState`；错误走 `error` 状态横幅或 `useToast()`。
2. **用户操作 → 变更**：按钮/表单调用 `api.xxx()` POST/PUT/DELETE，成功后直接本地状态更新或触发整页重拉。
3. **长任务（世界构建/写作引擎）**：先 `POST` 拿到 job/run id，再开 `EventSource` 订阅 SSE URL，逐事件更新细粒度状态（阶段/进度/日志/当前节点/待审），终止事件（`job_done`/`done`/`error`）时关闭连接并做一次最终 `GET` 同步。
4. **鉴权**：`client.ts` 每次请求带 JWT，401 时清 token 并派发事件，`App.tsx` 监听后弹出 `LoginDialog`；登录/注册成功后 token 存 localStorage 并回调更新顶层状态。

无路由级数据 loader、无请求缓存/去重、无乐观更新库——符合这是一个面向单一后端服务的小型内部控制面板的定位。
