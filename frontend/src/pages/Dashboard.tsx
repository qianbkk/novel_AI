import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, withConcurrency } from "../api/client";
import type { ChapterListItem, Project } from "../types";
import { useReveal } from "../hooks/useReveal";
import { useToast } from "../components/Toast";

function getGenreColor(genre?: string): string {
  switch (genre) {
    case "玄幻": return "linear-gradient(180deg, #6366F1 0%, #8B5CF6 100%)";
    case "仙侠": return "linear-gradient(180deg, #10B981 0%, #059669 100%)";
    case "都市": return "linear-gradient(180deg, #F59E0B 0%, #D97706 100%)";
    case "科幻": return "linear-gradient(180deg, #06B6D4 0%, #0284C7 100%)";
    case "悬疑": return "linear-gradient(180deg, #8B5CF6 0%, #6D28D9 100%)";
    case "历史": return "linear-gradient(180deg, #E11D48 0%, #BE123C 100%)";
    default: return "linear-gradient(180deg, #6366F1 0%, #4F46E5 100%)";
  }
}

function statusBadge(status: Project["status"]) {
  if (status === "ready") {
    return (
      <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, background: "rgba(16, 185, 129, 0.15)", border: "1px solid rgba(16, 185, 129, 0.35)", color: "#34D399", fontWeight: 600 }}>
        ✓ 已就绪
      </span>
    );
  }
  if (status === "worldbuilding") {
    return (
      <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, background: "rgba(245, 158, 11, 0.15)", border: "1px solid rgba(245, 158, 11, 0.35)", color: "#FBBF24", fontWeight: 600 }}>
        ⏳ 构建中
      </span>
    );
  }
  return (
    <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, background: "rgba(255, 255, 255, 0.06)", border: "1px solid rgba(255, 255, 255, 0.12)", color: "#94A3B8" }}>
      📝 草稿
    </span>
  );
}

function runningBadge(p: Project) {
  if (!p.active_run_command || !p.active_run_status) return null;
  const cmdLabels: Record<string, string> = {
    planner: "设定包",
    bootstrap: "黄金三章",
    init_arc: "大纲规划",
    run: "正文写作",
    run_draft: "草稿写作",
    dashboard: "质量扫描",
    scan: "一致性校验",
    fingerprint: "文风指纹",
  };
  const label = cmdLabels[p.active_run_command] || p.active_run_command;
  return (
    <span
      className="running-pulse"
      style={{
        fontSize: 11,
        padding: "2px 8px",
        borderRadius: 4,
        background: "rgba(99, 102, 241, 0.2)",
        border: "1px solid #6366F1",
        color: "#A5B4FC",
        fontWeight: 600,
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
      }}
      title={`${p.active_run_command} · 运行中`}
    >
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#818CF8", display: "inline-block", animation: "pulse 1.5s infinite" }} />
      正在 {label}…
    </span>
  );
}

function WritingJourney({ p, chs, projectId }: { p: Project; chs: ChapterListItem[]; projectId: string }) {
  const navigate = useNavigate();
  const steps: { key: string; label: string; icon: string; path: string }[] = [
    { key: "preprod",  label: "① 题材开篇", icon: "🔮", path: `/projects/${projectId}/theme` },
    { key: "world",    label: "② 世界设定", icon: "🌍", path: `/projects/${projectId}/worldbuild` },
    { key: "outline",  label: "③ 故事大纲", icon: "📜", path: `/projects/${projectId}/outline` },
    { key: "chapters", label: "④ 正文写作", icon: "✍️", path: `/projects/${projectId}/bridge` },
    { key: "reader",   label: "⑤ 目录阅读", icon: "📖", path: `/projects/${projectId}/chapters` },
  ];

  const hasWorld = p.status === "ready";
  const hasChapters = chs.length > 0;

  let currentIdx = 0;
  if (hasWorld) currentIdx = 2;
  if (hasChapters) currentIdx = 3;

  return (
    <div className="modern-card-journey" aria-label="创作旅程进度">
      {steps.map((s, i) => {
        const done = i < currentIdx;
        const current = i === currentIdx;
        const cls = done
          ? "modern-journey-step is-done"
          : current
          ? "modern-journey-step is-current"
          : "modern-journey-step is-pending";
        return (
          <button
            type="button"
            key={s.key}
            className={cls}
            onClick={(e) => {
              e.stopPropagation();
              navigate(s.path);
            }}
            title={`前往步骤：${s.label}`}
          >
            <span style={{ fontSize: 12, lineHeight: 1 }}>{done ? "✓" : s.icon}</span>
            <span style={{ fontSize: 10, letterSpacing: "-0.01em" }}>{s.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [chapterMap, setChapterMap] = useState<Record<string, ChapterListItem[]>>({});
  const [, setChapterLoadFailures] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [q, setQ] = useState(searchParams.get("q") || "");
  const [genre, setGenre] = useState(searchParams.get("genre") || "");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [availableGenres, setAvailableGenres] = useState<string[]>([]);
  const navigate = useNavigate();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);
  const toast = useToast();

  useEffect(() => {
    mountedRef.current = true;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (selectedIds.size > 0) {
        e.preventDefault();
        clearSelection();
      } else if (q || genre) {
        e.preventDefault();
        setQ(""); setGenre("");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("keydown", onKey);
    };
  }, [selectedIds.size, q, genre]);

  useReveal(rootRef);

  async function loadAll() {
    setError(null);
    try {
      const ps = await api.listProjects({ q, genre });
      if (!mountedRef.current) return;
      setProjects(ps);

      try {
        const allPs = await api.listProjects({});
        if (mountedRef.current) {
          setAvailableGenres(
            Array.from(new Set(allPs.map((p) => p.genre).filter(Boolean))).sort()
          );
        }
      } catch {
        if (mountedRef.current) {
          setAvailableGenres(
            Array.from(new Set(ps.map((p) => p.genre).filter(Boolean))).sort()
          );
        }
      }

      const results = await withConcurrency(4,
        ...ps.map((p) => () => api.listChapters(p.id).then(
          (chs) => ({ id: p.id, chs, failed: false }),
          (e: unknown) => {
            console.warn(`[Dashboard] listChapters failed for ${p.id}:`, e);
            return { id: p.id, chs: [] as ChapterListItem[], failed: true };
          }
        ))
      );
      const entries: Array<{ id: string; chs: ChapterListItem[]; failed: boolean }> =
        results.map((r) =>
          r.ok ? r.value : { id: "", chs: [], failed: true }
        );
      if (!mountedRef.current) return;
      setChapterMap(Object.fromEntries(entries.map(({ id, chs }) => [id, chs])));
      setChapterLoadFailures(Object.fromEntries(
        entries.map(({ id, failed }) => [id, failed])
      ));
    } catch (e) {
      if (!mountedRef.current) return;
      setError(String(e));
    }
  }

  useEffect(() => {
    const t = setTimeout(() => {
      const next = new URLSearchParams();
      if (q) next.set("q", q);
      if (genre) next.set("genre", genre);
      setSearchParams(next, { replace: true });
      loadAll();
    }, 300);
    return () => clearTimeout(t);
  }, [q, genre]);

  const totalWords = useMemo(
    () => Object.values(chapterMap).flat().reduce((a, c) => a + c.word_count, 0),
    [chapterMap],
  );

  const totalChapters = useMemo(
    () => Object.values(chapterMap).reduce((a, c) => a + c.length, 0),
    [chapterMap],
  );

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllVisible() {
    if (!projects) return;
    setSelectedIds(new Set(projects.map((p) => p.id)));
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  async function bulkDeleteSelected() {
    if (selectedIds.size === 0) return;
    if (!window.confirm(`确认删除已选 ${selectedIds.size} 个小说项目？此操作不可撤销。`)) return;
    setBulkBusy(true);
    try {
      const res = await api.bulkDeleteProjects(Array.from(selectedIds));
      toast.success(`已删除 ${res.deleted.length} 个项目`);
      setSelectedIds(new Set());
      await loadAll();
    } catch (e) {
      toast.error("批量删除失败", String(e));
    } finally {
      setBulkBusy(false);
    }
  }

  async function pinSelected(pinned: boolean) {
    if (selectedIds.size === 0) return;
    setBulkBusy(true);
    try {
      const results = await Promise.allSettled(
        Array.from(selectedIds).map((id) => api.pinProject(id, { pinned })),
      );
      const ok = results.filter((r) => r.status === "fulfilled").length;
      toast.success(`${pinned ? "置顶" : "取消置顶"} ${ok}/${selectedIds.size} 个项目`);
      setSelectedIds(new Set());
      await loadAll();
    } catch (e) {
      toast.error("置顶操作失败", String(e));
    } finally {
      setBulkBusy(false);
    }
  }

  async function togglePinOne(p: Project, e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await api.pinProject(p.id, { pinned: !p.pinned, pin_order: (p.pin_order || 0) + 1 });
      await loadAll();
    } catch (err) {
      toast.error("置顶切换失败", String(err));
    }
  }

  return (
    <div ref={rootRef} style={{ maxWidth: 1360, margin: "0 auto" }}>
      {/* 现代化 Hero Studio 看板顶栏 */}
      <div className="studio-hero">
        <div className="studio-hero__info">
          <div className="studio-hero__tag">
            <span>✨</span>
            <span>AI 长篇网络小说创作工坊 · v1.0</span>
          </div>
          <h1 className="studio-hero__title">
            落笔 · 创作者空间
          </h1>
          <p className="studio-hero__sub">
            题材画像 · 深度世界构建 · 多弧大纲规划 · 全自动人机协作连贯正文写作
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <div className="studio-hero__metrics">
            <div className="studio-metric-card">
              <span className="studio-metric-card__val">{projects ? projects.length : "—"}</span>
              <span className="studio-metric-card__label">📚 我的作品</span>
            </div>
            <div className="studio-metric-card">
              <span className="studio-metric-card__val">{totalChapters}</span>
              <span className="studio-metric-card__label">📖 累计章节</span>
            </div>
            <div className="studio-metric-card">
              <span className="studio-metric-card__val">
                {totalWords > 10000 ? `${(totalWords / 10000).toFixed(1)}万` : totalWords.toLocaleString()}
              </span>
              <span className="studio-metric-card__label">📝 总字数</span>
            </div>
          </div>

          <button
            type="button"
            className="btn btn-primary"
            style={{ padding: "11px 22px", fontSize: 14, fontWeight: 700, borderRadius: 10 }}
            onClick={() => navigate("/new")}
          >
            + 新建小说作品
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: 20 }}>
          <div>{error} — 后端服务连接异常，默认地址 <span className="text-mono">http://127.0.0.1:8132</span></div>
          <button
            type="button"
            className="btn btn-sm"
            style={{ marginTop: 8 }}
            onClick={loadAll}
          >
            重试连接
          </button>
        </div>
      )}

      {/* 搜索与题材快速筛选栏 */}
      <div className="studio-toolbar">
        <div className="studio-search-box">
          <span className="studio-search-icon">🔍</span>
          <input
            type="text"
            placeholder="搜索小说作品名 / 主角名 (Esc 清空)…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && q) {
                e.preventDefault();
                setQ("");
              }
            }}
            className="studio-search-input"
          />
          {q && (
            <button
              type="button"
              onClick={() => setQ("")}
              style={{
                position: "absolute",
                right: 8,
                top: "50%",
                transform: "translateY(-50%)",
                background: "transparent",
                border: "none",
                color: "#94A3B8",
                cursor: "pointer",
                fontSize: 16,
              }}
            >
              ×
            </button>
          )}
        </div>

        <div className="studio-genre-chips">
          <button
            type="button"
            className={`studio-chip ${!genre ? "is-active" : ""}`}
            onClick={() => setGenre("")}
          >
            全部题材
          </button>
          {availableGenres.map((g) => (
            <button
              key={g}
              type="button"
              className={`studio-chip ${genre === g ? "is-active" : ""}`}
              onClick={() => setGenre(genre === g ? "" : g)}
            >
              {g}
            </button>
          ))}
        </div>
      </div>

      {/* 批量操作工具栏 */}
      {selectedIds.size > 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 18px",
            marginBottom: 20,
            background: "rgba(99, 102, 241, 0.12)",
            border: "1px solid rgba(99, 102, 241, 0.35)",
            borderRadius: 12,
          }}
        >
          <span style={{ fontSize: 13, color: "#F8FAFC" }}>
            已勾选 <strong style={{ color: "#A5B4FC" }}>{selectedIds.size}</strong> 本作品
          </span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={clearSelection}
            disabled={bulkBusy}
          >
            取消选择 (Esc)
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={selectAllVisible}
            disabled={bulkBusy}
          >
            全选当前
          </button>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => pinSelected(true)}
            disabled={bulkBusy}
          >
            📌 批量置顶
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => pinSelected(false)}
            disabled={bulkBusy}
          >
            📍 取消置顶
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger"
            onClick={bulkDeleteSelected}
            disabled={bulkBusy}
          >
            🗑 批量删除
          </button>
        </div>
      )}

      {/* 空状态提示 */}
      {projects && projects.length === 0 && (
        <div
          style={{
            textAlign: "center",
            padding: "60px 20px",
            background: "#131724",
            border: "1px dashed rgba(255, 255, 255, 0.15)",
            borderRadius: 16,
            marginTop: 20,
          }}
        >
          <div style={{ fontSize: 36, marginBottom: 12 }}>✍️</div>
          <h3 style={{ margin: "0 0 8px", color: "#F8FAFC" }}>
            {q || genre ? "没有找到符合条件的小说作品" : "书库里还没有作品"}
          </h3>
          <p style={{ margin: "0 0 20px", color: "#94A3B8", fontSize: 14 }}>
            {q || genre ? "尝试清空搜索条件或切换其他题材分类" : "点击下方按钮创建第一本长篇小说，开启专属 AI 创作旅程"}
          </p>
          {q || genre ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => { setQ(""); setGenre(""); }}
            >
              清空搜索与筛选
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => navigate("/new")}
            >
              + 新建第一本小说
            </button>
          )}
        </div>
      )}

      {/* 小说卡片网格 */}
      {projects && projects.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))",
            gap: 20,
          }}
        >
          {projects.map((p) => {
            const chs = chapterMap[p.id] || [];
            const projectWords = chs.reduce((a, c) => a + c.word_count, 0);
            const spineGrad = getGenreColor(p.genre);

            return (
              <div
                key={p.id}
                className="modern-project-card"
                style={{ "--card-spine": spineGrad } as React.CSSProperties}
                onClick={() => {
                  if (p.active_run_command) {
                    navigate(`/projects/${p.id}/bridge`);
                  } else if (p.status === "ready") {
                    navigate(`/projects/${p.id}/outline`);
                  } else {
                    navigate(`/projects/${p.id}/theme`);
                  }
                }}
              >
                {/* 顶部标签栏与控制 */}
                <div className="modern-card-head">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(p.id)}
                      onChange={() => toggleSelect(p.id)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ width: 16, height: 16, cursor: "pointer", accentColor: "#6366F1" }}
                    />
                    <button
                      type="button"
                      onClick={(e) => togglePinOne(p, e)}
                      title={p.pinned ? "取消置顶" : "置顶小说"}
                      style={{
                        background: "transparent",
                        border: "none",
                        cursor: "pointer",
                        fontSize: 16,
                        lineHeight: 1,
                        padding: 0,
                        opacity: p.pinned ? 1 : 0.4,
                      }}
                    >
                      {p.pinned ? "📌" : "📍"}
                    </button>
                    <div className="modern-card-tags">
                      <span className="modern-card-tag" style={{ color: "#A5B4FC", fontWeight: 600 }}>
                        {p.genre || "综合题材"}
                      </span>
                      {p.audience && <span className="modern-card-tag">{p.audience}</span>}
                    </div>
                  </div>

                  <div>
                    {runningBadge(p) || statusBadge(p.status)}
                  </div>
                </div>

                {/* 小说标题 */}
                <h3 className="modern-card-title">
                  {p.title || "未命名长篇小说"}
                </h3>

                {/* 创作冲突 / 核心设定简介 */}
                <p
                  style={{
                    fontSize: 12.5,
                    color: "#94A3B8",
                    lineHeight: 1.5,
                    margin: 0,
                    overflow: "hidden",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    minHeight: 38,
                  }}
                >
                  {p.config_json?.main_conflict || "已配置长篇网络小说核心架构，可快速进入题材画像与世界观设定…"}
                </p>

                {/* 5 步创作旅程指示器 */}
                <WritingJourney p={p} chs={chs} projectId={p.id} />

                {/* 核心数据概览 */}
                <div className="modern-card-metrics">
                  <div>已写 <strong>{chs.length}</strong> 章</div>
                  <div>累计 <strong>{projectWords.toLocaleString()}</strong> 字</div>
                  {chs.length > 0 && (
                    <div>均 <strong>{Math.round(projectWords / chs.length)}</strong> 字/章</div>
                  )}
                  <div style={{ marginLeft: "auto", fontSize: 11, color: "#64748B" }}>
                    ID: {p.id.slice(0, 8)}
                  </div>
                </div>

                {/* 底部动作 Dock */}
                <div className="modern-card-actions">
                  <button
                    type="button"
                    className="modern-card-actions__primary"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/projects/${p.id}/bridge`);
                    }}
                  >
                    <span>▶</span>
                    <span>立即写作</span>
                  </button>

                  <div className="modern-card-actions__quick">
                    <button
                      type="button"
                      className="modern-quick-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate(`/projects/${p.id}/outline`);
                      }}
                      title="故事大纲"
                    >
                      📜 大纲
                    </button>
                    <button
                      type="button"
                      className="modern-quick-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate(`/projects/${p.id}/worldbuild`);
                      }}
                      title="世界设定"
                    >
                      🌍 设定
                    </button>
                    <button
                      type="button"
                      className="modern-quick-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate(`/projects/${p.id}/chapters`);
                      }}
                      title="章节目录"
                    >
                      📖 目录
                    </button>
                    <button
                      type="button"
                      className="modern-quick-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate(`/projects/${p.id}/characters`);
                      }}
                      title="角色档案"
                    >
                      👤 角色
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
