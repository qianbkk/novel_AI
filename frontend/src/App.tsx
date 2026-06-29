import { Routes, Route, NavLink, useLocation } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import NewProject from "./pages/NewProject";
import WorldBuild from "./pages/WorldBuild";
import Chapters from "./pages/Chapters";
import Providers from "./pages/Providers";
import RoleAssignments from "./pages/RoleAssignments";
import BridgeConsole from "./pages/BridgeConsole";

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

  return (
    <div className="app-shell">
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
          </div>
        )}

        <div className="sidebar-footer">
          <div>backend :8123</div>
          <div>frontend :5293</div>
        </div>
      </aside>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<NewProject />} />
          <Route path="/settings/providers" element={<Providers />} />
          <Route path="/settings/roles" element={<RoleAssignments />} />
          <Route path="/projects/:projectId/worldbuild" element={<WorldBuild />} />
          <Route path="/projects/:projectId/chapters" element={<Chapters />} />
          <Route path="/projects/:projectId/bridge" element={<BridgeConsole />} />
        </Routes>
      </main>
    </div>
  );
}
