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
          <div className="sidebar-brand__name">墨笺 · Novel AI</div>
          <div className="sidebar-brand__sub">FUSION · v0.1</div>
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
