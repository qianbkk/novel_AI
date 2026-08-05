/**
 * CharacterList — /projects/{pid}/characters
 *
 * 2026-08-05 修复：Dashboard 的 "👤 角色" 按钮文案承诺"角色卡 8 段 + 关系图谱 +
 * 势力阵营"，但 onClick 跳到 /worldbuild（WorldBuild 顶级 tab "人物阵营" 里 M04.1
 * 才有角色动态）。文案与行为不一致。
 *
 * 修法：给角色加独立路由 /projects/:projectId/characters，列表式陈列所有角色，
 * 点击进详情。Dashboard "角色" 按钮链到本路由。
 *
 * 后端 API：GET /projects/{pid}/characters 返 CharacterSummary[]。
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { CharacterSummary, Project } from "../types";

export default function CharacterList() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!projectId) return;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [p, cs] = await Promise.all([
          api.getProject(projectId).catch(() => null),
          api.listCharacters(projectId),
        ]);
        if (!mountedRef.current) return;
        setProject(p);
        setCharacters(cs);
      } catch (e) {
        if (!mountedRef.current) return;
        setError(String(e));
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    })();
  }, [projectId]);

  const filtered = characters.filter((c) => {
    if (roleFilter && c.role !== roleFilter) return false;
    if (query) {
      const q = query.toLowerCase();
      if (!c.name.toLowerCase().includes(q) &&
          !(c.role || "").toLowerCase().includes(q)) return false;
    }
    return true;
  });

  const roleOptions = Array.from(new Set(characters.map((c) => c.role || "未分配")));

  if (!projectId) return <div className="banner banner-danger">缺少项目 ID。</div>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">
            角色列表
            {project && <span className="badge-soft badge" style={{ marginLeft: 8 }}>{project.title || "未命名"}</span>}
          </h1>
          <div className="page-header__sub">
            共 {characters.length} 名 · 显示 {filtered.length} ·
            8 段结构化卡片 · 关系图与势力阵营在「世界观/人物阵营」tab
          </div>
        </div>
        <div className="page-header__actions">
          <button
            type="button"
            className="btn"
            onClick={() => navigate(`/projects/${projectId}/worldbuild`)}
          >
            关系图谱 / 势力阵营 →
          </button>
        </div>
      </div>

      {error && <div className="banner banner-danger">{error}</div>}

      <div className="dashboard-toolbar" style={{ display: "flex", gap: 12, alignItems: "center", margin: "16px 0", flexWrap: "wrap" }}>
        <input
          type="text"
          placeholder="搜索角色名 / 角色定位..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="dashboard-search-input"
        />
        <div className="dashboard-genre-chips">
          <button
            className={`genre-chip ${!roleFilter ? "genre-chip--active" : ""}`}
            onClick={() => setRoleFilter("")}
          >
            全部
          </button>
          {roleOptions.map((r) => (
            <button
              key={r}
              className={`genre-chip ${roleFilter === r ? "genre-chip--active" : ""}`}
              onClick={() => setRoleFilter(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="loading-text">加载中…</p>}

      {!loading && characters.length === 0 && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state__title">还没有角色</div>
            <div className="empty-state__hint">运行「世界观构建」后会生成基础角色阵容</div>
            <div className="empty-state__action">
              <button
                className="btn btn-primary"
                onClick={() => navigate(`/projects/${projectId}/worldbuild`)}
              >
                去跑世界构建 →
              </button>
            </div>
          </div>
        </div>
      )}

      {!loading && characters.length > 0 && filtered.length === 0 && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state__title">没匹配的角色</div>
            <div className="empty-state__action">
              <button className="btn" onClick={() => { setQuery(""); setRoleFilter(""); }}>
                清除筛选
              </button>
            </div>
          </div>
        </div>
      )}

      {filtered.length > 0 && (
        <div className="legislation-grid">
          {filtered.map((c) => (
            <div
              key={c.id}
              className="legislation-card"
              style={{ cursor: "pointer" }}
              onClick={() => navigate(`/projects/${projectId}/characters/${c.id}`)}
              title="点开查看完整角色卡 8 段"
            >
              <div className="legislation-card__head">
                <span className="legislation-card__title">{c.name}</span>
                <span className="legislation-card__kicker">{c.role || "未分配"}</span>
              </div>
              <div className="legislation-card__desc" style={{ minHeight: 36 }}>
                {c.identity || c.gender || "（该角色尚未生成详细描述）"}
              </div>
              <div className="legislation-card__chips" style={{ marginTop: 8 }}>
                <span className="legislation-card__chip">id · {c.id.slice(0, 6)}</span>
                <span className="legislation-card__chip" style={{ color: "var(--accent-strong)" }}>
                  → 查看 8 段详情
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
