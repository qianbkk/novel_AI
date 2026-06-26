import { Routes, Route, Link } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import NewProject from "./pages/NewProject";
import WorldBuild from "./pages/WorldBuild";
import Chapters from "./pages/Chapters";
import Providers from "./pages/Providers";
import RoleAssignments from "./pages/RoleAssignments";
import BridgeConsole from "./pages/BridgeConsole";

export default function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header__title">
          📖 AI 小说写作助手
          <small>世界构建 · 一致性校验 · 语义检索</small>
        </div>
        <nav>
          <Link to="/">项目</Link>
          <Link to="/settings/providers">Provider</Link>
          <Link to="/settings/roles">角色配置</Link>
        </nav>
      </header>

      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/new" element={<NewProject />} />
        <Route path="/settings/providers" element={<Providers />} />
        <Route path="/settings/roles" element={<RoleAssignments />} />
        <Route path="/projects/:projectId/worldbuild" element={<WorldBuild />} />
        <Route path="/projects/:projectId/chapters" element={<Chapters />} />
        <Route path="/projects/:projectId/bridge" element={<BridgeConsole />} />
      </Routes>
    </div>
  );
}
