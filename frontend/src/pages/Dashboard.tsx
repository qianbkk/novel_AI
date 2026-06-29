import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Project } from "../types";

function statusBadge(status: Project["status"]) {
  if (status === "ready") return <span className="badge-stamp">已就绪</span>;
  if (status === "worldbuilding") return <span className="badge-soft">构建中</span>;
  return <span className="badge-draft">草稿</span>;
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">我的项目</h1>
          <div className="page-header__sub">
            {projects
              ? `共 ${projects.length} 个项目 · 选一个继续推进`
              : "加载中…"}
          </div>
        </div>
        <div className="page-header__actions">
          <button
            className="btn btn-primary"
            onClick={() => navigate("/new")}
          >
            + 新建小说
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-danger">
          {error} — 后端没起来？默认地址{" "}
          <span className="text-mono">http://localhost:8123</span>
        </div>
      )}

      {projects && projects.length === 0 && (
        <div className="card">
          <div className="empty-state">
            还没有项目
            <div className="empty-state__hint">
              点右上角"新建小说"，填个标题和题材，从世界构建开始
            </div>
          </div>
        </div>
      )}

      {projects && projects.length > 0 && (
        <div className="grid-cards">
          {projects.map((p) => (
            <div
              key={p.id}
              className="project-card"
              onClick={() =>
                navigate(
                  p.status === "ready"
                    ? `/projects/${p.id}/bridge`
                    : `/projects/${p.id}/worldbuild`,
                )
              }
            >
              <div className="project-card__title">
                {p.title || "未命名小说"}
              </div>
              <div className="project-card__meta">
                {p.genre || "未分类"}
                {p.audience ? ` · ${p.audience}` : ""}
              </div>
              <div className="project-card__foot">
                {statusBadge(p.status)}
                <span className="text-faint text-mono">{p.id.slice(0, 8)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
