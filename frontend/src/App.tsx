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
import { LoginDialog } from "./components/LoginDialog";
import { api, getStoredToken } from "./api/client";

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

  // ─── Phase 4：登录态管理 ───
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

        {/* 登录态指示器（Phase 4） — 用户主动登录 / 匿名标记 */}
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
          <div>backend :8132</div>
          <div>frontend :5293</div>
        </div>
      </aside>

      <main className="app-main" id="main-content" tabIndex={-1}>
        <div className="page-fade" key={location.pathname}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<NewProject />} />
          <Route path="/settings/providers" element={<Providers />} />
          <Route path="/settings/roles" element={<RoleAssignments />} />
          <Route path="/projects/:projectId/worldbuild" element={<WorldBuild />} />
          <Route path="/projects/:projectId/chapters" element={<Chapters />} />
          <Route path="/projects/:projectId/bridge" element={<BridgeConsole />} />
          <Route path="/projects/:projectId/rules" element={<RuleCenter />} />
          {/* Phase 4: 角色卡详情页 */}
          <Route path="/projects/:projectId/characters/:characterId" element={<CharacterCard />} />
        </Routes>
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
