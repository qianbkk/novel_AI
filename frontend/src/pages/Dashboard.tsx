import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Project } from "../types";

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
      <div className="flex-between" style={{ marginBottom: 20 }}>
        <h2 style={{ margin: 0 }}>我的项目</h2>
        <button className="btn btn-primary" onClick={() => navigate("/new")}>
          + 新建小说
        </button>
      </div>

      {error && <div className="banner banner-danger">{error}（后端是不是没启动？默认地址 http://localhost:8123）</div>}

      {!projects && !error && <p className="loading-text">加载中…</p>}

      {projects && projects.length === 0 && (
        <div className="card">
          <div className="empty-state">还没有创建小说项目，点右上角"新建小说"开始第一个故事。</div>
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
              <div className="project-card__title">{p.title || "未命名小说"}</div>
              <div className="text-muted" style={{ fontSize: "0.85rem", marginBottom: 10 }}>
                {p.genre} · {p.audience || "未设定受众"}
              </div>
              <span className={`badge ${p.status === "ready" ? "badge-ready" : "badge-draft"}`}>
                {p.status === "ready"
                  ? "✍️ 就绪 · 点进去写章节"
                  : p.status === "worldbuilding"
                    ? "构建中"
                    : "草稿"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
