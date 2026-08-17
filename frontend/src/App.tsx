import { useEffect, useState } from "react";
import { Routes, Route, NavLink, useLocation } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import NewProject from "./pages/NewProject";
import WorldBuild from "./pages/WorldBuild";
import Chapters from "./pages/Chapters";
import Providers from "./pages/Providers";
import RoleAssignments from "./pages/RoleAssignments";
import BridgeConsole from "./pages/BridgeConsole";
import RuleCenter from "./pages/RuleCenter";
import CharacterCard from "./pages/CharacterCard";
import CharacterList from "./pages/CharacterList";  // 2026-08-05：角色专属索引页（Dashboard 角色按钮目标）
import Outline from "./pages/Outline";  // 弧级大纲管理
import ChapterReader from "./pages/ChapterReader";  // 章节阅读器（独立页面）
import NotFound from "./pages/NotFound";  // 2026-07-25: 404 兜底（之前无 path="*" → 白屏）
import ThemeOpening from "./pages/ThemeOpening";  // v1.0 Stage I: 主题与开篇
import { LoginDialog } from "./components/LoginDialog";
import { ErrorBoundary } from "./components/ErrorBoundary";  // 2026-07-25: 路由级错误兜底
import { api, getStoredToken } from "./api/client";
import { useBackendHealth } from "./hooks/useBackendHealth";

const GLOBAL_LINKS = [
  { to: "/", label: "项目" },
  { to: "/settings/providers", label: "模型供应商" },
  { to: "/settings/roles", label: "角色绑定" },
];

export default function App() {
  const location = useLocation();
  // Detect "in a project" route for sub-nav
  const projectMatch = location.pathname.match(/^\/projects\/([^/]+)/);
  const projectId = projectMatch?.[1];

  // 2026-08-18：后端实时健康（每 5s 探测 /health）
  // sidebar footer 展示，让用户随时知道后端在不在
  const backend = useBackendHealth();

  // ─── 登录态管理 ───
  // 优先从 localStorage 恢复；没有 token 时显示"匿名"。
  // 暴露 meOrNull 检查真有效性（token 存在但失效会清掉并显示"匿名"）。
  const [authEmail, setAuthEmail] = useState<string | null>(null);
  const [authDialogOpen, setAuthDialogOpen] = useState(false);

  useEffect(() => {
    // mount 时：若 localStorage 有 token 就静默验签 (/auth/me)。
    // 失败则清掉；成功则把 email 显示在侧栏。
    if (!getStoredToken()) {
      setAuthEmail(null);
      return;
    }
    api.meOrNull().then((u) => setAuthEmail(u?.email ?? null));

    // 监听后端 401 事件（仅 production 模式会真正发）
    const onAuthRequired = () => setAuthDialogOpen(true);
    window.addEventListener("novel_ai:auth_required", onAuthRequired);
    return () => window.removeEventListener("novel_ai:auth_required", onAuthRequired);
  }, []);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳到主内容
      </a>
      <aside className="app-sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand__name">
            <svg
              className="sidebar-brand__icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              {/* 羽毛笔 quill */}
              <path d="M20 3c-3 0-7 2-11 6-3 3-5 7-5 10l5-5c4-4 6-8 6-11z" />
              <path d="M4 19l5-5" />
              <path d="M9 14l1 1" />
              <path d="M13 10l1 1" />
              <path d="M16 7l1 1" />
            </svg>
            落笔
          </div>
          <div className="sidebar-brand__sub">FirstDraft · AI 写小说</div>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section__label">导航</div>
          {GLOBAL_LINKS.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.to === "/"}
              className={({ isActive }) =>
                `sidebar-link${isActive ? " is-active" : ""}`
              }
            >
              <span className="sidebar-link__dot" />
              {l.label}
            </NavLink>
          ))}
        </div>

        {/* 登录态指示器 — 用户主动登录 / 匿名标记 */}
        <div className="sidebar-section">
          <div className="sidebar-section__label">账号</div>
          {authEmail ? (
            <>
              <div
                title={authEmail}
                style={{
                  padding: "4px 0",
                  fontSize: 13,
                  color: "var(--fg, #000)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {authEmail}
              </div>
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  api.logout();
                  setAuthEmail(null);
                }}
                className="sidebar-link"
                style={{ fontSize: 12, opacity: 0.7 }}
              >
                <span className="sidebar-link__dot" />
                登出
              </a>
            </>
          ) : (
            <a
              href="#"
              onClick={(e) => { e.preventDefault(); setAuthDialogOpen(true); }}
              className="sidebar-link"
            >
              <span className="sidebar-link__dot" />
              登录 / 注册
            </a>
          )}
        </div>

        {projectId && (
          <div className="sidebar-section">
            <div className="sidebar-section__label">当前项目</div>
            <NavLink
              to={`/projects/${projectId}/worldbuild`}
              className={({ isActive }) =>
                `sidebar-link${isActive ? " is-active" : ""}`
              }
            >
              <span className="sidebar-link__dot" />
              世界构建
            </NavLink>
            {/* 弧级大纲管理 */}
            <NavLink
              to={`/projects/${projectId}/outline`}
              className={({ isActive }) =>
                `sidebar-link${isActive ? " is-active" : ""}`
              }
            >
              <span className="sidebar-link__dot" />
              大纲管理
            </NavLink>
            <NavLink
              to={`/projects/${projectId}/bridge`}
              className={({ isActive }) =>
                `sidebar-link${isActive ? " is-active" : ""}`
              }
            >
              <span className="sidebar-link__dot" />
              写作控制台
            </NavLink>
            <NavLink
              to={`/projects/${projectId}/chapters`}
              className={({ isActive }) =>
                `sidebar-link${isActive ? " is-active" : ""}`
              }
            >
              <span className="sidebar-link__dot" />
              章节管理
            </NavLink>
            <NavLink
              to={`/projects/${projectId}/rules`}
              className={({ isActive }) =>
                `sidebar-link${isActive ? " is-active" : ""}`
              }
            >
              <span className="sidebar-link__dot" />
              规则中心
            </NavLink>
          </div>
        )}

        <div className="sidebar-footer">
          {/* 2026-08-18：后端实时状态灯（用户报告 #6：WorldBuild 看不到，
              怀疑后端没起来；现在随时看得到）。三态：检测中/运行中/未响应。 */}
          <BackendStatusBadge state={backend} />
          <div style={{ fontSize: 11, opacity: 0.65 }}>frontend :5293</div>
        </div>
      </aside>

      <main className="app-main" id="main-content" tabIndex={-1}>
        <div className="page-fade" key={location.pathname}>
        {/* 2026-07-25：错误边界包住 <Routes>，任何子组件渲染抛错降级到友好错误页，
            不再整个 SPA 白屏。404 由 path="*" 兜底。 */}
        <ErrorBoundary key={location.pathname}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<NewProject />} />
          <Route path="/settings/providers" element={<Providers />} />
          <Route path="/settings/roles" element={<RoleAssignments />} />
          <Route path="/projects/:projectId/worldbuild" element={<WorldBuild />} />
          <Route path="/projects/:projectId/chapters" element={<Chapters />} />
          <Route path="/projects/:projectId/bridge" element={<BridgeConsole />} />
          <Route path="/projects/:projectId/rules" element={<RuleCenter />} />
          {/* 角色列表（2026-08-05：Dashboard "👤 角色" 按钮目标） */}
          <Route path="/projects/:projectId/characters" element={<CharacterList />} />
          {/* 角色卡详情页 */}
          <Route path="/projects/:projectId/characters/:characterId" element={<CharacterCard />} />
          {/* 弧级大纲管理 */}
          <Route path="/projects/:projectId/outline" element={<Outline />} />
          <Route path="/projects/:projectId/theme" element={<ThemeOpening />} />
          {/* 章节阅读器（独立页面替代 Dialog） */}
          <Route path="/projects/:projectId/chapter/:chapterNo" element={<ChapterReader />} />
          {/* 404 兜底（必须放最后 — React Router v6 第一个匹配即返回） */}
          <Route path="*" element={<NotFound />} />
        </Routes>
        </ErrorBoundary>
        </div>
      </main>

      <LoginDialog
        open={authDialogOpen}
        onClose={() => setAuthDialogOpen(false)}
        onAuthed={(email) => setAuthEmail(email)}
      />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// BackendStatusBadge — sidebar 实时后端健康灯（2026-08-18）
// 设计动机：用户报告 #6 — WorldBuild 看不到内容，怀疑后端没起来。
// 之前没有任何后端状态可见性，前端只能通过请求失败推测。
//
// 三态：
//   checking：黄色 · 旋转点 · "正在检测…"
//   up：绿色实心点 · "backend :8132 · ok (47ms)"
//   down：红色实心点 · 错误摘要 + 「重启 dev.bat」提示
//
// 不做启动/停止按钮：CLAUDE.md「失败要响亮，不替用户决策」；
// dev.bat 是用户本地的 bat，浏览器无权调；只给可点击的本地 dev.bat 提示。
// ════════════════════════════════════════════════════════════════

import type { BackendState } from "./hooks/useBackendHealth";

function BackendStatusBadge({ state }: { state: BackendState }) {
  const [expanded, setExpanded] = useState(false);

  if (state.kind === "checking") {
    return (
      <div
        className="backend-status backend-status--checking"
        title="正在探测后端 /health…"
        style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: "50%",
          background: "var(--color-warn, #E0A55F)",
          display: "inline-block",
          animation: "pulse 1.4s ease-in-out infinite",
        }} />
        backend :8132 · 检测中…
      </div>
    );
  }

  if (state.kind === "up") {
    const lat = state.latencyMs != null ? ` (${state.latencyMs}ms)` : "";
    return (
      <div
        className="backend-status backend-status--up"
        title={`后端 /health 200${lat}`}
        style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--color-moss, #6FBC8A)" }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: "50%",
          background: "var(--color-moss, #6FBC8A)",
          display: "inline-block",
        }} />
        backend :8132 · ok{lat}
      </div>
    );
  }

  // down
  return (
    <div
      className="backend-status backend-status--down"
      style={{ fontSize: 11, color: "var(--color-stamp, #E06C5F)" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{
          width: 8, height: 8, borderRadius: "50%",
          background: "var(--color-stamp, #E06C5F)",
          display: "inline-block",
        }} />
        backend :8132 · 未响应
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? "收起错误详情" : "展开错误详情"}
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "none",
            color: "inherit",
            cursor: "pointer",
            fontSize: 10,
            opacity: 0.7,
          }}
        >
          {expanded ? "▴" : "▾"}
        </button>
      </div>
      {expanded && (
        <div
          style={{
            marginTop: 6,
            padding: "6px 8px",
            background: "var(--color-stamp-soft, rgba(224,108,95,0.12))",
            borderRadius: 4,
            fontFamily: "var(--font-mono)",
            fontSize: 10.5,
            lineHeight: 1.45,
            wordBreak: "break-all",
          }}
        >
          <div style={{ marginBottom: 4 }}>{state.error}</div>
          <div style={{ opacity: 0.75, fontStyle: "italic" }}>
            在项目根目录执行 <code>dev.bat start-all</code> 启动后端
          </div>
        </div>
      )}
    </div>
  );
}
