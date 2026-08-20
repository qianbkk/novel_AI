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
    case "奇幻": return "linear-gradient(180deg, #EC4899 0%, #BE185D 100%)";
    case "末世": return "linear-gradient(180deg, #EF4444 0%, #B91C1C 100%)";
    case "游戏": return "linear-gradient(180deg, #3B82F6 0%, #1D4ED8 100%)";
    case "武侠": return "linear-gradient(180deg, #14B8A6 0%, #0F766E 100%)";
    case "言情": return "linear-gradient(180deg, #F43F5E 0%, #E11D48 100%)";
    case "军事": return "linear-gradient(180deg, #84CC16 0%, #4D7C0F 100%)";
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
  const [allProjects, setAllProjects] = useState<Project[]>([]);
  const [chapterMap, setChapterMap] = useState<Record<string, ChapterListItem[]>>({});
  const [, setChapterLoadFailures] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();

  // 筛选与检索状态
  const [q, setQ] = useState(searchParams.get("q") || "");
  const [genre, setGenre] = useState(searchParams.get("genre") || "");
  const [statusFilter, setStatusFilter] = useState(searchParams.get("status") || "all");
  const [pinnedOnly, setPinnedOnly] = useState(searchParams.get("pinned") === "1");
  const [sortBy, setSortBy] = useState(searchParams.get("sort") || "pinned_first");

  // 选择与批量状态
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const navigate = useNavigate();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);
  const toast = useToast();

  useEffect(() => {
    mountedRef.current = true;
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      if (e.key === "Escape") {
        if (selectedIds.size > 0) {
          e.preventDefault();
          setSelectedIds(new Set());
        } else if (q || genre || statusFilter !== "all" || pinnedOnly) {
          e.preventDefault();
          resetFilters();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("keydown", onKey);
    };
  }, [selectedIds.size, q, genre, statusFilter, pinnedOnly]);

  useReveal(rootRef);

  function resetFilters() {
    setQ("");
    setGenre("");
    setStatusFilter("all");
    setPinnedOnly(false);
    setSortBy("pinned_first");
  }

  async function loadAll() {
    setError(null);
    try {
      // 1. 获取根据筛选条件过滤的项目列表
      const ps = await api.listProjects({
        q: q.trim() || undefined,
        genre: genre || undefined,
        status: statusFilter !== "all" ? statusFilter : undefined,
        pinned_only: pinnedOnly ? true : undefined,
        sort_by: sortBy,
      });
      if (!mountedRef.current) return;
      setProjects(ps);

      // 2. 获取全局项目列表用于统计题材与总数
      try {
        const fullList = await api.listProjects({});
        if (mountedRef.current) {
          setAllProjects(fullList);
        }
      } catch {
        if (mountedRef.current) {
          setAllProjects(ps);
        }
      }

      // 3. 并发拉取章节信息
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

  // 依赖防抖触发加载与同步 URL 参数
  useEffect(() => {
    const t = setTimeout(() => {
      const next = new URLSearchParams();
      if (q) next.set("q", q);
      if (genre) next.set("genre", genre);
      if (statusFilter !== "all") next.set("status", statusFilter);
      if (pinnedOnly) next.set("pinned", "1");
      if (sortBy !== "pinned_first") next.set("sort", sortBy);
      setSearchParams(next, { replace: true });
      loadAll();
    }, 250);
    return () => clearTimeout(t);
  }, [q, genre, statusFilter, pinnedOnly, sortBy]);

  // 统计题材分布
  const genreCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of allProjects) {
      if (p.genre) {
        counts[p.genre] = (counts[p.genre] || 0) + 1;
      }
    }
    return counts;
  }, [allProjects]);

  const availableGenres = useMemo(() => {
    return Object.keys(genreCounts).sort((a, b) => genreCounts[b] - genreCounts[a]);
  }, [genreCounts]);

  const totalWords = useMemo(
    () => Object.values(chapterMap).flat().reduce((a, c) => a + c.word_count, 0),
    [chapterMap],
  );

  const totalChapters = useMemo(
    () => Object.values(chapterMap).reduce((a, c) => a + c.length, 0),
    [chapterMap],
  );

  // 选择控制
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

  function invertSelection() {
    if (!projects) return;
    setSelectedIds((prev) => {
      const next = new Set<string>();
      for (const p of projects) {
        if (!prev.has(p.id)) next.add(p.id);
      }
      return next;
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  // 批量操作
  async function bulkDeleteSelected() {
    if (selectedIds.size === 0) return;
    if (!window.confirm(`确认删除已选 ${selectedIds.size} 个小说项目？此操作将永久移除相关世界观与已写章节。`)) return;
    setBulkBusy(true);
    try {
      const res = await api.bulkDeleteProjects(Array.from(selectedIds));
      toast.success(`已删除 ${res.deleted.length} 个小说项目`);
      setSelectedIds(new Set());
      await loadAll();
    } catch (e) {
      toast.error("批量删除失败", String(e));
    } finally {
      setBulkBusy(false);
    }
  }

  async function bulkPinSelected(pinned: boolean) {
    if (selectedIds.size === 0) return;
    setBulkBusy(true);
    try {
      const res = await api.bulkPinProjects(Array.from(selectedIds), pinned);
      toast.success(`已成功${pinned ? "置顶" : "取消置顶"} ${res.updated.length} 个项目`);
      setSelectedIds(new Set());
      await loadAll();
    } catch (e) {
      toast.error("批量置顶操作失败", String(e));
    } finally {
      setBulkBusy(false);
    }
  }

  // 单个置顶切换（带乐观 UI 响应）
  async function togglePinOne(p: Project, e: React.MouseEvent) {
    e.stopPropagation();
    const newPinned = !p.pinned;
    // 乐观更新 UI
    setProjects((prev) =>
      prev ? prev.map((item) => (item.id === p.id ? { ...item, pinned: newPinned } : item)) : prev
    );
    try {
      await api.pinProject(p.id, { pinned: newPinned });
      toast.success(newPinned ? `已置顶《${p.title || "未命名小说"}》` : `已取消置顶《${p.title || "未命名小说"}》`);
      await loadAll();
    } catch (err) {
      toast.error("置顶切换失败", String(err));
      await loadAll();
    }
  }

  const isFiltering = Boolean(q || genre || statusFilter !== "all" || pinnedOnly);

  return (
    <div ref={rootRef} style={{ maxWidth: 1360, margin: "0 auto", paddingBottom: 80 }}>
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
              <span className="studio-metric-card__val">{allProjects.length || (projects ? projects.length : "—")}</span>
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

      {/* 现代化搜索、筛选与多维排序控制台 */}
      <div
        style={{
          background: "#131724",
          border: "1px solid rgba(255, 255, 255, 0.08)",
          borderRadius: 14,
          padding: "16px 20px",
          marginBottom: 20,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        {/* 第一行：搜索框 + 状态筛选 + 仅置顶 + 排序下拉 */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          {/* 搜索框 */}
          <div className="studio-search-box" style={{ flex: 1, minWidth: 260 }}>
            <span className="studio-search-icon">🔍</span>
            <input
              type="text"
              placeholder="搜索小说作品名 / 主角名 (Esc 清空)…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
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

          {/* 状态分段选择 */}
          <div style={{ display: "inline-flex", background: "#0D1019", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8, padding: 3 }}>
            {[
              { key: "all", label: "全部状态" },
              { key: "ready", label: "✓ 已就绪" },
              { key: "worldbuilding", label: "⏳ 构建中" },
              { key: "draft", label: "📝 草稿" },
            ].map((st) => (
              <button
                key={st.key}
                type="button"
                onClick={() => setStatusFilter(st.key)}
                style={{
                  background: statusFilter === st.key ? "rgba(99, 102, 241, 0.25)" : "transparent",
                  color: statusFilter === st.key ? "#A5B4FC" : "#94A3B8",
                  border: `1px solid ${statusFilter === st.key ? "#6366F1" : "transparent"}`,
                  borderRadius: 6,
                  padding: "5px 10px",
                  fontSize: 12,
                  cursor: "pointer",
                  fontWeight: statusFilter === st.key ? 600 : 400,
                  transition: "all 0.15s ease",
                }}
              >
                {st.label}
              </button>
            ))}
          </div>

          {/* 仅看置顶开关 */}
          <button
            type="button"
            onClick={() => setPinnedOnly(!pinnedOnly)}
            style={{
              padding: "6px 12px",
              borderRadius: 8,
              fontSize: 12.5,
              cursor: "pointer",
              background: pinnedOnly ? "rgba(245, 158, 11, 0.2)" : "#0D1019",
              border: `1px solid ${pinnedOnly ? "#F59E0B" : "rgba(255, 255, 255, 0.12)"}`,
              color: pinnedOnly ? "#FBBF24" : "#94A3B8",
              fontWeight: pinnedOnly ? 600 : 400,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              transition: "all 0.15s ease",
            }}
          >
            <span>📌</span>
            <span>仅看置顶</span>
          </button>

          {/* 排序方式下拉 */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 12, color: "#64748B" }}>排序:</span>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="studio-filter-select"
            >
              <option value="pinned_first">📌 置顶优先 (默认)</option>
              <option value="updated_at">🕒 最近修改</option>
              <option value="created_at">📅 创建时间</option>
              <option value="title">🔤 作品名称</option>
            </select>
          </div>
        </div>

        {/* 第二行：题材胶囊筛选 */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: "#64748B", marginRight: 4 }}>题材分类:</span>
          <button
            type="button"
            className={`studio-chip ${!genre ? "is-active" : ""}`}
            onClick={() => setGenre("")}
          >
            全部 ({allProjects.length || 0})
          </button>
          {availableGenres.map((g) => (
            <button
              key={g}
              type="button"
              className={`studio-chip ${genre === g ? "is-active" : ""}`}
              onClick={() => setGenre(genre === g ? "" : g)}
            >
              {g} ({genreCounts[g] || 0})
            </button>
          ))}
        </div>

        {/* 当有筛选生效时，展示已生效筛选气泡与一键重置 */}
        {isFiltering && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", paddingTop: 8, borderTop: "1px solid rgba(255, 255, 255, 0.05)" }}>
            <span style={{ fontSize: 11.5, color: "#64748B" }}>当前筛选条件：</span>
            {q && (
              <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 999, background: "rgba(99, 102, 241, 0.18)", color: "#A5B4FC", display: "inline-flex", alignItems: "center", gap: 4 }}>
                🔍 "{q}"
                <button type="button" onClick={() => setQ("")} style={{ background: "transparent", border: "none", color: "#A5B4FC", cursor: "pointer", padding: 0 }}>×</button>
              </span>
            )}
            {genre && (
              <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 999, background: "rgba(99, 102, 241, 0.18)", color: "#A5B4FC", display: "inline-flex", alignItems: "center", gap: 4 }}>
                题材: {genre}
                <button type="button" onClick={() => setGenre("")} style={{ background: "transparent", border: "none", color: "#A5B4FC", cursor: "pointer", padding: 0 }}>×</button>
              </span>
            )}
            {statusFilter !== "all" && (
              <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 999, background: "rgba(99, 102, 241, 0.18)", color: "#A5B4FC", display: "inline-flex", alignItems: "center", gap: 4 }}>
                状态: {statusFilter}
                <button type="button" onClick={() => setStatusFilter("all")} style={{ background: "transparent", border: "none", color: "#A5B4FC", cursor: "pointer", padding: 0 }}>×</button>
              </span>
            )}
            {pinnedOnly && (
              <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 999, background: "rgba(245, 158, 11, 0.2)", color: "#FBBF24", display: "inline-flex", alignItems: "center", gap: 4 }}>
                📌 仅置顶
                <button type="button" onClick={() => setPinnedOnly(false)} style={{ background: "transparent", border: "none", color: "#FBBF24", cursor: "pointer", padding: 0 }}>×</button>
              </span>
            )}
            <button
              type="button"
              onClick={resetFilters}
              style={{
                background: "transparent",
                border: "none",
                color: "#94A3B8",
                fontSize: 11.5,
                cursor: "pointer",
                textDecoration: "underline",
                marginLeft: "auto",
              }}
            >
              重置所有筛选
            </button>
          </div>
        )}
      </div>

      {/* 悬浮式批量操作 Dock (选中项目时优雅浮现) */}
      {selectedIds.size > 0 && (
        <div className="studio-floating-dock">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#6366F1" }} />
            <span style={{ fontSize: 13, color: "#F8FAFC", fontWeight: 600 }}>
              已选择 <strong>{selectedIds.size}</strong> 本作品
            </span>
          </div>

          <div style={{ width: 1, height: 18, background: "rgba(255, 255, 255, 0.15)" }} />

          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={selectAllVisible}
            disabled={bulkBusy}
            style={{ fontSize: 12 }}
          >
            ☑️ 全选
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={invertSelection}
            disabled={bulkBusy}
            style={{ fontSize: 12 }}
          >
            🔄 反选
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={clearSelection}
            disabled={bulkBusy}
            style={{ fontSize: 12 }}
          >
            ✖ 取消 (Esc)
          </button>

          <div style={{ width: 1, height: 18, background: "rgba(255, 255, 255, 0.15)" }} />

          <button
            type="button"
            className="btn btn-sm"
            onClick={() => bulkPinSelected(true)}
            disabled={bulkBusy}
            style={{ fontSize: 12 }}
          >
            📌 批量置顶
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => bulkPinSelected(false)}
            disabled={bulkBusy}
            style={{ fontSize: 12 }}
          >
            📍 取消置顶
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger"
            onClick={bulkDeleteSelected}
            disabled={bulkBusy}
            style={{ fontSize: 12 }}
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
            {isFiltering ? "没有找到符合条件的小说作品" : "书库里还没有作品"}
          </h3>
          <p style={{ margin: "0 0 20px", color: "#94A3B8", fontSize: 14 }}>
            {isFiltering ? "尝试调整搜索关键词、切换题材分类或重置筛选条件" : "点击下方按钮创建第一本长篇小说，开启专属 AI 创作旅程"}
          </p>
          {isFiltering ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={resetFilters}
            >
              清空搜索与筛选条件
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
            const isSelected = selectedIds.has(p.id);

            return (
              <div
                key={p.id}
                className={`modern-project-card ${isSelected ? "is-selected" : ""} ${p.pinned ? "is-pinned" : ""}`}
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
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    {/* 选择 Checkbox */}
                    <div
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleSelect(p.id);
                      }}
                      style={{
                        padding: "4px",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => {}}
                        style={{ width: 16, height: 16, cursor: "pointer", accentColor: "#6366F1", margin: 0 }}
                      />
                    </div>

                    {/* 置顶切换按钮 */}
                    <button
                      type="button"
                      onClick={(e) => togglePinOne(p, e)}
                      title={p.pinned ? "已置顶（点击取消置顶）" : "置顶此作品"}
                      style={{
                        background: p.pinned ? "rgba(245, 158, 11, 0.2)" : "rgba(255, 255, 255, 0.05)",
                        border: `1px solid ${p.pinned ? "#F59E0B" : "rgba(255, 255, 255, 0.1)"}`,
                        borderRadius: 6,
                        cursor: "pointer",
                        fontSize: 13,
                        lineHeight: 1,
                        padding: "4px 7px",
                        color: p.pinned ? "#FBBF24" : "#64748B",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        transition: "all 0.15s ease",
                      }}
                    >
                      <span>{p.pinned ? "📌" : "📍"}</span>
                      {p.pinned && <span style={{ fontSize: 11, fontWeight: 600 }}>置顶</span>}
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
