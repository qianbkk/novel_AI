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
  const projectMatch = location.pathname.match(/^\/projects\/([^/]+)/);
  const projectId = projectMatch?.[1];
  const [projectTitle, setProjectTitle] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setProjectTitle(null);
      return;
    }
    let cancelled = false;
    api.getProject(projectId).then((p) => {
      if (!cancelled) setProjectTitle(p.title || "未命名小说");
    }).catch(() => {
      if (!cancelled) setProjectTitle("作品空间");
    });
    return () => { cancelled = true; };
  }, [projectId]);

  const backend = useBackendHealth();
  const [authEmail, setAuthEmail] = useState<string | null>(null);
  const [authDialogOpen, setAuthDialogOpen] = useState(false);

  useEffect(() => {
    if (!getStoredToken()) {
      setAuthEmail(null);
      return;
    }
    api.meOrNull().then((u) => setAuthEmail(u?.email ?? null));

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
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M20 3c-3 0-7 2-11 6-3 3-5 7-5 10l5-5c4-4 6-8 6-11z" />
              <path d="M4 19l5-5" />
              <path d="M9 14l1 1" />
              <path d="M13 10l1 1" />
              <path d="M16 7l1 1" />
            </svg>
            落笔 · Studio
          </div>
          <div className="sidebar-brand__sub">FirstDraft · AI 创作者空间</div>
        </div>

        {/* 全局书库与系统配置 */}
        <div className="sidebar-section">
          <div className="sidebar-section__label">全局中心</div>
          <NavLink to="/" end className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
            <span className="sidebar-link__icon">📚</span>
            <span>我的作品库</span>
          </NavLink>
          <NavLink to="/settings/providers" className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
            <span className="sidebar-link__icon">⚡</span>
            <span>模型供应商</span>
          </NavLink>
          <NavLink to="/settings/roles" className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
            <span className="sidebar-link__icon">🎭</span>
            <span>角色模型绑定</span>
          </NavLink>
        </div>

        {/* 当前作品工作流 */}
        {projectId && (
          <div className="sidebar-section">
            <div className="sidebar-section__label">
              <span title={projectTitle || ""}>当前作品</span>
              <span style={{ fontSize: 10, color: "var(--color-accent-strong)", opacity: 0.85, maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {projectTitle || "创作空间"}
              </span>
            </div>

            <div className="sidebar-group-title">① 设定筹备</div>
            <NavLink to={`/projects/${projectId}/theme`} className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
              <span className="sidebar-link__icon">🔮</span>
              <span>题材与开篇</span>
            </NavLink>
            <NavLink to={`/projects/${projectId}/worldbuild`} className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
              <span className="sidebar-link__icon">🌍</span>
              <span>世界观构建</span>
            </NavLink>

            <div className="sidebar-group-title" style={{ marginTop: 8 }}>② 结构与正文</div>
            <NavLink to={`/projects/${projectId}/outline`} className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
              <span className="sidebar-link__icon">📜</span>
              <span>故事大纲</span>
            </NavLink>
            <NavLink to={`/projects/${projectId}/bridge`} className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
              <span className="sidebar-link__icon">✍️</span>
              <span>写作控制台</span>
            </NavLink>

            <div className="sidebar-group-title" style={{ marginTop: 8 }}>③ 目录与资产</div>
            <NavLink to={`/projects/${projectId}/chapters`} className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
              <span className="sidebar-link__icon">📖</span>
              <span>章节目录</span>
            </NavLink>
            <NavLink to={`/projects/${projectId}/characters`} className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
              <span className="sidebar-link__icon">👤</span>
              <span>角色档案库</span>
            </NavLink>
            <NavLink to={`/projects/${projectId}/rules`} className={({ isActive }) => `sidebar-link${isActive ? " is-active" : ""}`}>
              <span className="sidebar-link__icon">⚖️</span>
              <span>AI 规则中心</span>
            </NavLink>
          </div>
        )}

        {/* 登录态指示器 */}
        <div className="sidebar-section" style={{ marginTop: "auto" }}>
          <div className="sidebar-section__label">账户</div>
          {authEmail ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "4px 8px" }}>
              <div
                title={authEmail}
                style={{
                  fontSize: 12,
                  color: "var(--color-fg-2)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 140,
                }}
              >
                👤 {authEmail}
              </div>
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  api.logout();
                  setAuthEmail(null);
                }}
                style={{ fontSize: 11, color: "var(--color-fg-4)", textDecoration: "none" }}
              >
                登出
              </a>
            </div>
          ) : (
            <a
              href="#"
              onClick={(e) => { e.preventDefault(); setAuthDialogOpen(true); }}
              className="sidebar-link"
              style={{ fontSize: 12.5 }}
            >
              <span className="sidebar-link__icon">🔑</span>
              <span>登录 / 注册</span>
            </a>
          )}
        </div>

        <div className="sidebar-footer">
          <BackendStatusBadge state={backend} />
          <div style={{ fontSize: 11, color: "var(--color-fg-4)" }}>frontend :5293 · v1.0</div>
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
