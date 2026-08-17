import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, withConcurrency } from "../api/client";
import type { ChapterListItem, Project } from "../types";
import { useReveal } from "../hooks/useReveal";
import { useToast } from "../components/Toast";

// 2026-07-25 抽离（修 P1-2 短板 inline style 收编）：
// chipStyle(active) JS 函数生成 CSSProperties 模式改用 CSS class ——
// .genre-chip + .genre-chip--active 已在 styles.css 定义。
function statusBadge(status: Project["status"]) {
  if (status === "ready") return <span className="badge-stamp">已就绪</span>;
  if (status === "worldbuilding") return <span className="badge-soft">构建中</span>;
  return <span className="badge-draft">草稿</span>;
}

// 2026-07-24 修复（运行态可见性）：Project.status 是 draft/worldbuilding/ready
// 三态之一，但 bridge.run 跑时 status 不变 — 用户看不到"正在跑"。
// 后端 /projects 现在带 active_run_command/status 字段：非空就显示"运行中" badge。
// 点击直接跳到 BridgeConsole 看实时 SSE 日志。
function runningBadge(p: Project) {
  if (!p.active_run_command || !p.active_run_status) return null;
  const cmdLabels: Record<string, string> = {
    planner: "生成设定包",
    bootstrap: "黄金三章",
    init_arc: "初始化弧",
    run: "写章节",
    run_draft: "写章节(草稿)",
    dashboard: "质量看板",
    scan: "一致性扫描",
    fingerprint: "文风指纹",
    push: "推送",
    pull: "拉取",
    export: "导出",
  };
  const label = cmdLabels[p.active_run_command] || p.active_run_command;
  const status = p.active_run_status === "running" ? "运行中" : "排队中";
  return (
    <span
      className="badge-stamp running-pulse"
      title={`${p.active_run_command} · ${status} · 开始于 ${p.active_run_started_at || "?"}`}
      aria-label={`正在 ${label}`}
    >
      ⟳ {label}
    </span>
  );
}

// 6 大模块元数据：显示在顶栏罗盘
const MODULES = [
  { idx: "M01", title: "多重记忆防御", sub: "三道防线·可控推理", metric: "L1 弧段 + L2 衔接 + L3 压缩" },
  { idx: "M02", title: "世界立法", sub: "GIS · 力量 · 物权", metric: "世界构建完成后即生效" },
  { idx: "M03", title: "叙事工程", sub: "七要素 + 多模式大纲", metric: "欲望/阻碍/行动/结果/意外/转折/结局" },
  { idx: "M04", title: "角色生命周期", sub: "数字实体 · 因果引擎", metric: "存续状态实时同步" },
  { idx: "M05", title: "章节执行", sub: "实时人机协作", metric: "每章含场景+伏笔+状态" },
  { idx: "M06", title: "AI 治理", sub: "规则中心 · 文笔指纹", metric: "毒舌模式 + 去味" },
];

// 2026-08-18（架构修复 #7）：
// 把项目从"功能卡"改为"写作旅程"。小白用户进入 Dashboard 后
// 看到的不再是「这个项目有多少章/多少字」，而是「我现在在哪一步、下一步是什么」。
//
// 5 步旅程对应用户实际操作链路：
//   1. 创建项目（已创建项目必经过）
//   2. 题材画像 + 主题（v1.0 Pre-Production）
//   3. 世界构建（10 阶段）
//   4. 大纲（弧级）
//   5. 写章节（每章 + 上下文）
//
// 当前 step 从真实数据推断：project.status / outline 数量 / 章节数。
// 不拉额外接口：用已加载的 chapterMap 推断「写章节」是否开始；
// 题材画像+主题是否就绪暂用 project.status 兜底（status=ready 表明已完成世界构建；
// 严格 v1.0 拆开后端需要补 endpoint，但那是另一任务范围）。
function WritingJourney({ p, chs }: { p: Project; chs: ChapterListItem[] }) {
  // 推断 5 步完成度
  const steps: { key: string; label: string; icon: string }[] = [
    { key: "created",  label: "创建", icon: "📝" },
    { key: "preprod",  label: "题材+主题", icon: "🎭" },
    { key: "world",    label: "世界观", icon: "🌍" },
    { key: "outline",  label: "大纲", icon: "📜" },
    { key: "chapters", label: "写章节", icon: "✍️" },
  ];
  // 1. 创建 — 项目存在 = done
  // 2. 题材+主题 — 项目 status 已是 ready 时认为完成（更精细需要查 genre/theme endpoint；
  //    这里保守先用 status；后续补 endpoint 后再细化）
  // 3. 世界观 — project.status === "ready"
  // 4. 大纲 — outline 数 > 0（前端无 outline 缓存，先用 hasOutline placeholder）
  //    但 WorldBuild ready 后通常会 run bootstrap 落 arc_plans；
  //    在前端加载大纲前粗略判断 = status===ready
  // 5. 写章节 — chapters.length > 0
  const hasWorld = p.status === "ready";
  const hasChapters = chs.length > 0;

  // 当前 step index（高亮）
  let currentIdx = 1; // 默认在 preprod
  if (hasWorld) currentIdx = 3;  // 大纲
  if (hasChapters) currentIdx = 4; // 写章节
  if (!hasWorld && !hasChapters) currentIdx = 1; // 还在 preprod

  return (
    <div className="writing-journey" aria-label="写作旅程进度">
      {steps.map((s, i) => {
        const done = i < currentIdx || (i === 0); // 创建永远 done
        const current = i === currentIdx;
        const cls = done ? "writing-journey__step writing-journey__step--done"
                       : current ? "writing-journey__step writing-journey__step--current"
                                 : "writing-journey__step writing-journey__step--pending";
        return (
          <div key={s.key} className={cls}>
            <span className="writing-journey__icon">{done ? "✓" : s.icon}</span>
            <span className="writing-journey__label">{s.label}</span>
          </div>
        );
      })}
    </div>
  );
}

function ModuleCompass({ projects, chapterMap }: { projects: Project[]; chapterMap: Record<string, ChapterListItem[]> }) {
  const totalChapters = Object.values(chapterMap).reduce((a, c) => a + c.length, 0);
  const totalWords = Object.values(chapterMap)
    .flat()
    .reduce((a, c) => a + c.word_count, 0);
  const ready = projects.filter((p) => p.status === "ready").length;
  // 罗盘进度 = 已构建项目比例 * 0.4 + 已写章节比例 * 0.4 + 字数比例 * 0.2
  const arcPct = Math.min(
    100,
    Math.round(
      (ready / Math.max(1, projects.length)) * 40 +
        Math.min(40, (totalChapters / 200) * 40) +
        Math.min(20, (Math.log10(Math.max(1, totalWords)) / 6) * 20),
    ),
  );
  const R = 76;
  const C = 2 * Math.PI * R;
  const offset = C * (1 - arcPct / 100);

  return (
    <div className="module-compass reveal">
      {/* 背景墨滴 SVG 装饰 */}
      <div className="ink-drop-bg ink-drop-bg--soft">
        <svg viewBox="0 0 600 200" preserveAspectRatio="xMidYMid slice">
          <defs>
            <radialGradient id="ink-grad" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#E06C5F" stopOpacity="0.18" />
              <stop offset="100%" stopColor="#E06C5F" stopOpacity="0" />
            </radialGradient>
          </defs>
          <circle cx="540" cy="40" r="120" fill="url(#ink-grad)" />
          <circle cx="60" cy="180" r="90" fill="url(#ink-grad)" opacity="0.6" />
          <path
            d="M 480 30 q 10 20 0 40 q -10 -20 0 -40 z"
            fill="#6B8AFD"
            opacity="0.10"
          />
        </svg>
      </div>

      <div className="module-compass__title">
        落笔 · FirstDraft
        <span className="module-compass__sub">6 大模块导览 · 长篇工业化</span>
      </div>

      <div className="module-compass__grid">
        <div className="module-compass__dial" aria-label="整体进度">
          <svg viewBox="0 0 200 200">
            <defs>
              <linearGradient id="dial-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#6B8AFD" />
                <stop offset="50%" stopColor="#93A9FF" />
                <stop offset="100%" stopColor="#E06C5F" />
              </linearGradient>
            </defs>
            {/* 刻度环 */}
            {Array.from({ length: 36 }).map((_, i) => {
              const a = (i / 36) * Math.PI * 2 - Math.PI / 2;
              const x1 = 100 + Math.cos(a) * 88;
              const y1 = 100 + Math.sin(a) * 88;
              const x2 = 100 + Math.cos(a) * 92;
              const y2 = 100 + Math.sin(a) * 92;
              return (
                <line
                  key={i}
                  x1={x1} y1={y1} x2={x2} y2={y2}
                  stroke={i % 9 === 0 ? "var(--accent-strong)" : "var(--border-strong)"}
                  strokeWidth={i % 9 === 0 ? 1.4 : 0.6}
                  strokeLinecap="round"
                  opacity={i % 9 === 0 ? 0.9 : 0.4}
                />
              );
            })}
            <circle cx="100" cy="100" r={R} className="dial-arc-bg" />
            <circle
              cx="100"
              cy="100"
              r={R}
              className="dial-arc-fg"
              strokeDasharray={C}
              strokeDashoffset={offset}
              transform="rotate(-90 100 100)"
            />
            {/* 4 个方位文字 */}
            {[
              { x: 100, y: 18, t: "主线" },
              { x: 182, y: 104, t: "立法" },
              { x: 100, y: 192, t: "执行" },
              { x: 18, y: 104, t: "治理" },
            ].map((p) => (
              <text key={p.t} x={p.x} y={p.y} textAnchor="middle" className="dial-tick-text">
                {p.t}
              </text>
            ))}
            {/* 中心读数 */}
            <text x="100" y="96" textAnchor="middle" className="dial-label">整体</text>
            <text x="100" y="116" textAnchor="middle" fill="var(--text)" fontFamily="var(--font-display)" fontSize="22" fontWeight={700}>
              {arcPct}%
            </text>
            {/* 中心小光点 */}
            <circle cx="100" cy="138" r="3" className="dial-pulse" />
          </svg>
        </div>

        <div className="module-compass__cells">
          {MODULES.map((m) => (
            <div className="compass-cell" key={m.idx}>
              <div className="compass-cell__head">
                <span className="compass-cell__index">{m.idx}</span>
                {m.title}
              </div>
              <div className="compass-cell__sub">{m.sub}</div>
              <div className="compass-cell__metric">{m.metric}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [chapterMap, setChapterMap] = useState<Record<string, ChapterListItem[]>>({});
  const [chapterLoadFailures, setChapterLoadFailures] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [q, setQ] = useState(searchParams.get("q") || "");
  const [genre, setGenre] = useState(searchParams.get("genre") || "");
  // 2026-08-08 任务 #12：多选 + 删除 + 置顶。
  // selectedIds: 当前多选中的项目 id。selectedCount > 0 时 toolbar 显示"已选 N 项"+ 操作按钮。
  // bulkBusy: 批量删除进行中（防双击重复发请求）。
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  // 2026-08-18：全量题材池（独立于筛选条件），让 genre chip 始终展示完整题材列表，
  // 避免「筛了 genre1 之后就看不到 genre2 切回去」这种用户报告的逻辑漏洞。
  const [availableGenres, setAvailableGenres] = useState<string[]>([]);
  const navigate = useNavigate();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);
  const toast = useToast();

  useEffect(() => {
    mountedRef.current = true;
    // 2026-08-18：Esc 键统一清除搜索 + 多选（最直观的「撤销」入口）
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      // 如果焦点在 input 内由 input 自身 onKeyDown 处理；这里处理 input 外的 Esc
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
      // 2026-08-18 修复（用户报告「筛选逻辑漏洞百出」）：
      // 同时拉一次 "全部项目"（不带 q/genre）来填充"题材池"。
      // 否则当 genre 切到 X 后，前端 chip 列表只显示 X，
      // 用户再也点不回 Y。素材池独立于筛选，互不干扰。
      try {
        const allPs = await api.listProjects({});
        if (mountedRef.current) {
          setAvailableGenres(
            Array.from(new Set(allPs.map((p) => p.genre).filter(Boolean))).sort()
          );
        }
      } catch {
        // 拉全量失败 → fallback 到当前结果里的题材（保守但可用）
        if (mountedRef.current) {
          setAvailableGenres(
            Array.from(new Set(ps.map((p) => p.genre).filter(Boolean))).sort()
          );
        }
      }
      // 审计 #2（2026-07-22）：串行 + 4 并发限制，避免全量并发导致
      // 2026-07-25 接入 client.withConcurrency 替换手写 chunk 循环（4 并发）。
      // helper 是本 commit (ad9d8f2) 为收编这块而抽的；现在实际接入。
      // withConcurrency 用 sliding window（不是固定 chunk），语义更优：
      // worker 完成后立即取下一个任务，4 个 worker 始终满载。
      const results = await withConcurrency(4,
        ...ps.map((p) => () => api.listChapters(p.id).then(
          (chs) => ({ id: p.id, chs, failed: false }),
          (e: unknown) => {
            // 审计 #1（2026-07-22）：不再静默吞单个项目章节加载失败，
            // console.warn 让开发者看到，同时 hasError=true 让 UI 能区分
            // 「真的没有章节」与「加载失败」（前端用 hasError 标灰）。
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
      // chapterMap: id -> chapters；failures: id -> true（前端可标灰）
      setChapterMap(Object.fromEntries(entries.map(({ id, chs }) => [id, chs])));
      setChapterLoadFailures(Object.fromEntries(
        entries.map(({ id, failed }) => [id, failed])
      ));
    } catch (e) {
      if (!mountedRef.current) return;
      setError(String(e));
    }
  }

  // debounce 300ms：当 q/genre 变化时同步到 URL 并重新拉取
  useEffect(() => {
    const t = setTimeout(() => {
      const next = new URLSearchParams();
      if (q) next.set("q", q);
      if (genre) next.set("genre", genre);
      setSearchParams(next, { replace: true });
      loadAll();
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, genre]);

  const totalWords = useMemo(
    () => Object.values(chapterMap).flat().reduce((a, c) => a + c.word_count, 0),
    [chapterMap],
  );

  // 2026-08-08 任务 #12 — 选中切换（复选框回调，阻止冒泡到卡片导航）
  // 用 Set 而不是数组：toggle O(1)，selectedIds.size 算已选数量 O(1)。
  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // 2026-08-18：全选 / 反选当前可见项目（保持 UI 整洁 + 批量操作高效）
  function selectAllVisible() {
    if (!projects) return;
    setSelectedIds(new Set(projects.map((p) => p.id)));
  }
  function clearSelection() {
    setSelectedIds(new Set());
  }

  async function bulkDeleteSelected() {
    if (selectedIds.size === 0) return;
    if (!window.confirm(`确认删除已选 ${selectedIds.size} 个项目？此操作不可撤销。`)) return;
    setBulkBusy(true);
    try {
      const res = await api.bulkDeleteProjects(Array.from(selectedIds));
      toast.success(`已删除 ${res.deleted.length} 个项目${res.skipped.length ? `（${res.skipped.length} 个无权跳过）` : ""}`);
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
      // 顺序 POST：每个项目单独 PUT /pin（后端无 batch 接口）。
      // 失败用 Promise.allSettled 收集，不让一条失败 abort 全部。
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
    e.stopPropagation();  // 不触发卡片 onClick 导航
    try {
      await api.pinProject(p.id, { pinned: !p.pinned, pin_order: (p.pin_order || 0) + 1 });
      await loadAll();
    } catch (err) {
      toast.error("置顶切换失败", String(err));
    }
  }

  return (
    <div ref={rootRef}>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">我的项目</h1>
          <div className="page-header__sub">
            {error
              ? "项目加载失败"
              : projects
                ? `共 ${projects.length} 个项目 · ${Object.values(chapterMap).flat().length} 章 · ${totalWords.toLocaleString()} 字`
                : "加载中…"}
          </div>
        </div>
        <div className="page-header__actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => navigate("/new")}
            aria-label="新建小说"
          >
            + 新建小说
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-danger" role="alert">
          <div>{error} — 后端没起来？默认地址 <span className="text-mono">http://localhost:8132</span></div>
          <button
            type="button"
            className="btn"
            style={{ marginTop: 10 }}  /* 2026-07-25：单一 inline style（margin-top 没法 className 化） */
            onClick={loadAll}
            aria-label="重试加载项目"
          >
            重试
          </button>
        </div>
      )}

      {/* 搜索 + 筛选区 */}
      <div className="dashboard-toolbar" style={{ display: "flex", gap: 12, alignItems: "center", margin: "16px 0", flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: "1 1 240px", maxWidth: 360 }}>
          <input
            type="text"
            placeholder="搜索项目名 / 主角名…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && q) {
                e.preventDefault();
                setQ("");
              }
            }}
            className="dashboard-search-input"
            style={{ width: "100%", paddingRight: q ? 32 : 12 }}
            aria-label="搜索项目"
          />
          {q && (
            <button
              type="button"
              onClick={() => setQ("")}
              aria-label="清除搜索"
              title="清除搜索（Esc）"
              style={{
                position: "absolute",
                right: 6,
                top: "50%",
                transform: "translateY(-50%)",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                fontSize: 16,
                color: "var(--text-muted)",
                padding: 4,
                lineHeight: 1,
              }}
            >
              ×
            </button>
          )}
        </div>
        <div className="dashboard-genre-chips" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {/* 2026-08-18 修复：全部 chip 总是显示，避免切 genre 后失去回退入口 */}
          <button
            className={`genre-chip ${!genre ? "genre-chip--active" : ""}`}
            onClick={() => setGenre("")}
            aria-pressed={!genre}
            title="显示所有题材"
          >
            全部
          </button>
          {/* 2026-08-18 修复：chip 列表展示用户**已配置**的所有题材，
             不能用 (projects || []) — 那只能看到当前筛选命中的题材，
             一旦切到 genre1 就再也点不回去 genre2（用户报告的逻辑漏洞）。
             这里改成读一个独立的"全量题材池"，避免 client side 漏列。 */}
          {availableGenres.map((g) => (
            <button
              key={g}
              className={`genre-chip ${genre === g ? "genre-chip--active" : ""}`}
              onClick={() => setGenre(genre === g ? "" : g)}
              aria-pressed={genre === g}
              title={genre === g ? `点击取消「${g}」筛选` : `只显示「${g}」题材的项目`}
            >
              {g}
            </button>
          ))}
        </div>
        {(q || genre) && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => { setQ(""); setGenre(""); }}
            aria-label="清除所有筛选"
          >
            清除筛选
          </button>
        )}
      </div>

      {/* 2026-08-08 任务 #12：选中条（多选 + 批量操作）。
          selectedIds 非空时才显示，避免每页都有一条空 toolbar 干扰视觉。
          设计：放工具栏右侧（不影响搜索输入框），半透明背景 + 边框提示"现在是批量模式"。
          2026-08-18 修复（用户反馈「批量管理逻辑粗糙」）：
          - 显示 已选 N/M（M=当前可见项目数），用户能直观看到「全选当前」的目标数
          - 加「全选当前」「清除选中」两个快捷按钮 + Esc 键清除选中
          - 已选数 = 当前可见数时「全选当前」禁用，已选数 = 0 时显示一个轻量的提示 */}
      {projects && projects.length > 0 && (
        <div
          className="bulk-toolbar"
          role="region"
          aria-label="批量操作栏"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "8px 14px",
            margin: "8px 0 16px",
            background: selectedIds.size > 0 ? "var(--surface-soft, #f5f1ea)" : "transparent",
            border: selectedIds.size > 0 ? "1px solid var(--accent, #6B8AFD)" : "1px dashed var(--border-2)",
            borderRadius: 10,
          }}
        >
          {selectedIds.size > 0 ? (
            <>
              <span style={{ fontSize: 13, color: "var(--ink, #2b2b2b)" }}>
                已选 <strong style={{ color: "var(--accent, #6B8AFD)" }}>{selectedIds.size}</strong>
                <span style={{ color: "var(--text-muted, #9098B0)" }}> / {projects.length}</span> 项
              </span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={clearSelection}
                disabled={bulkBusy}
                aria-label="取消多选（Esc）"
                title="取消多选（Esc）"
              >
                取消选择
              </button>
            </>
          ) : (
            <>
              <span style={{ fontSize: 12, color: "var(--text-muted, #9098B0)" }}>
                💡 勾选项目卡片可批量操作（置顶 / 删除）
              </span>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={selectAllVisible}
                aria-label="全选当前可见项目"
              >
                全选当前
              </button>
            </>
          )}
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className="btn"
            onClick={() => pinSelected(true)}
            disabled={bulkBusy || selectedIds.size === 0}
            aria-label="置顶选中项"
          >
            📌 置顶选中
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => pinSelected(false)}
            disabled={bulkBusy || selectedIds.size === 0}
            aria-label="取消置顶选中项"
          >
            📍 取消置顶
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={bulkDeleteSelected}
            disabled={bulkBusy || selectedIds.size === 0}
            aria-label="删除选中项"
          >
            🗑 删除选中
          </button>
        </div>
      )}

      {/* 五期：罗盘折叠为可选装饰，主体项目列表上移到首屏 */}
      {projects && projects.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--text-muted)" }}>
            模块罗盘（装饰性概览 · 点击展开）
          </summary>
          <div style={{ marginTop: 12 }}>
            <ModuleCompass projects={projects} chapterMap={chapterMap} />
          </div>
        </details>
      )}

      {projects && projects.length === 0 && (q || genre) && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state__title">没找到匹配的项目</div>
            <div className="empty-state__hint">
              {q && <>搜索 &quot;{q}&quot; </>}
              {genre && <>· 类型 &quot;{genre}&quot; </>}
              没有结果
            </div>
            <div className="empty-state__action">
              <button
                className="btn"
                onClick={() => { setQ(""); setGenre(""); }}
              >
                清除筛选
              </button>
            </div>
          </div>
        </div>
      )}

      {projects && projects.length === 0 && !q && !genre && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state__icon" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="1.5"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </div>
            <div className="empty-state__title">还没有项目</div>
            <div className="empty-state__hint">
              点右上角「新建小说」，填个标题和题材，从世界构建开始
            </div>
            <div className="empty-state__action">
              <button
                className="btn btn-primary"
                onClick={() => navigate("/new")}
              >
                + 新建小说
              </button>
            </div>
          </div>
        </div>
      )}

      {projects && projects.length > 0 && (
        <div className="grid-cards">
          {projects.map((p, i) => {
            const chs = chapterMap[p.id] || [];
            const recent = chs.slice(-3).reverse();
            const projectWords = chs.reduce((a, c) => a + c.word_count, 0);
            const arcPct = Math.min(100, Math.round((chs.length / 200) * 100));
            // 弧曲线数据：取最近 12 章的累计字数
            const lastN = chs.slice(-12);
            const wps = lastN.map((c, idx) => ({ x: idx, y: c.word_count }));
            return (
              <div
                key={p.id}
                className={`project-card reveal reveal--delay-${Math.min(5, i + 1)}`}
                onClick={() =>
                  navigate(
                    // 2026-08-18（架构修复 #7）：点击跳「当前所在步骤」对应页，
                    // 让小白用户点开项目卡就到该做的下一步。
                    p.active_run_command
                      ? `/projects/${p.id}/bridge`  // 2026-07-24：跑中跳 BridgeConsole 看 SSE
                      : p.status === "ready"
                      ? `/projects/${p.id}/outline`
                      : `/projects/${p.id}/theme`,
                  )
                }
              >
                {/* 2026-08-08 任务 #12：复选框 + 置顶状态指示。
                    两者放左上角同一区域，避免和右上 runningBadge 冲突。
                    复选框 stopPropagation 不触发卡片导航。
                    置顶图标点击切换置顶，pinned=true 时高亮。 */}
                <div
                  style={{
                    position: "absolute",
                    top: 10,
                    left: 10,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    zIndex: 2,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(p.id)}
                    onChange={() => toggleSelect(p.id)}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`选中项目 ${p.title || p.id.slice(0, 8)}`}
                    style={{ width: 18, height: 18, cursor: "pointer" }}
                  />
                  <button
                    type="button"
                    onClick={(e) => togglePinOne(p, e)}
                    title={p.pinned ? "取消置顶" : "置顶"}
                    aria-label={p.pinned ? `取消置顶 ${p.title || p.id.slice(0, 8)}` : `置顶 ${p.title || p.id.slice(0, 8)}`}
                    style={{
                      background: "transparent",
                      border: "none",
                      padding: 2,
                      cursor: "pointer",
                      fontSize: 18,
                      lineHeight: 1,
                      color: p.pinned ? "var(--accent, #6B8AFD)" : "var(--ink-soft, #b0a89a)",
                    }}
                  >
                    {p.pinned ? "📌" : "📍"}
                  </button>
                </div>

                {/* 卡片装饰羽毛笔 SVG */}
                <svg className="ink-splash-corner" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M50 8c-7 0-17 5-26 14-7 7-12 17-12 24l12-12c10-10 14-19 14-26z" stroke="var(--accent-strong)" />
                  <path d="M12 46l12-12" stroke="var(--stamp)" />
                  <path d="M22 36l2 2" stroke="var(--stamp)" />
                </svg>

                <div className="project-card__title">
                  {p.title || "未命名小说"}
                </div>
                <div className="project-card__meta">
                  {p.genre || "未分类"}
                  {p.audience ? ` · ${p.audience}` : ""}
                </div>
                {/* 2026-07-23 修复（问题 #7）：项目创建/修改时间。
                    之前后端 ProjectOut schema 没暴露 created_at/updated_at，
                    前端项目列表没法区分先后；现在 schema + types 已加，
                    卡片标题下方加一行小字显示时间。 */}
                <div className="project-card__time" style={{ fontSize: 11, color: "var(--ink-soft)", marginTop: 4 }}>
                  {p.created_at ? `创建 ${new Date(p.created_at).toLocaleString("zh-CN", { hour12: false })}` : ""}
                  {p.updated_at && p.updated_at !== p.created_at && p.updated_at !== null
                    ? ` · 更新 ${new Date(p.updated_at).toLocaleString("zh-CN", { hour12: false })}`
                    : ""}
                </div>

                {/* 2026-08-18：写作旅程 stepper（架构修复 #7）。
                    用户报告「前端布局不合理，要让小白打开就知道项目内容/如何用」。
                    把"功能清单"改为"用户旅程" — 5 步从创建到写章节，
                    当前所在步骤高亮（蓝色），已完成步骤打勾（绿色），
                    未开始步骤灰显。点击项目卡即跳到当前步骤对应页面。 */}
                <WritingJourney p={p} chs={chs} />

                {/* 项目产出概览。
                    这里原本是三行标着 L1/L2/L3 的"三道记忆防线"，分母还是
                    /5、/12 —— 但本项目的记忆只有 L2（热/冷/约束）和 L5（弧归档），
                    L1/L3 根本不存在，那三行的数值全是拿 chapters.length 和
                    log10(字数) 硬算的。列表页给每张卡片拉一次真实记忆不划算，
                    所以改成如实展示已有数据；真实分层记忆看写作控制台的
                    「分层记忆快照」面板。 */}
                <div className="memory-stack" style={{ marginTop: 10, gap: 4 }}>
                  <div className="memory-row memory-row--l2" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">章</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      已写 {chs.length} 章
                    </span>
                    <span className="memory-row__count">{p.status === "ready" ? "设定已建立" : "设定草拟中"}</span>
                  </div>
                  <div className="memory-row memory-row--l3" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">字</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      累计 {projectWords.toLocaleString()} 字
                    </span>
                    <span className="memory-row__count">
                      {chs.length ? `均 ${Math.round(projectWords / chs.length).toLocaleString()}/章` : "—"}
                    </span>
                  </div>
                </div>

                {/* 弧进度条 + 弧曲线 */}
                <div className="project-card__progress">
                  <div className="arc-pill" style={{ marginBottom: 4 }}>
                    <span>弧进度</span>
                    <span className="arc-pill__bar"><span style={{ transform: `scaleX(${arcPct / 100})` }} /></span>
                    <span>{arcPct}%</span>
                  </div>
                  <div className="progress-track" style={{ height: 3, margin: 0 }}>
                    <div className="progress-fill" style={{ width: `${arcPct}%` }} />
                  </div>
                  {wps.length > 1 && (
                    <div className="arc-curve" aria-hidden="true">
                      <svg viewBox={`0 0 ${Math.max(40, wps.length * 12)} 64`} preserveAspectRatio="none">
                        <defs>
                          <linearGradient id={`arc-grad-${p.id}`} x1="0" x2="1" y1="0" y2="0">
                            <stop offset="0%" stopColor="var(--accent)" />
                            <stop offset="100%" stopColor="var(--accent-strong)" />
                          </linearGradient>
                        </defs>
                        <line
                          x1="0" y1="32" x2={Math.max(40, wps.length * 12)} y2="32"
                          className="arc-curve__bg-line"
                        />
                        {(() => {
                          const W = Math.max(40, wps.length * 12);
                          const max = Math.max(1, ...wps.map((d) => d.y));
                          const pts = wps.map((d, i) => {
                            const x = (i / Math.max(1, wps.length - 1)) * W;
                            const y = 60 - (d.y / max) * 50;
                            return `${x.toFixed(1)},${y.toFixed(1)}`;
                          });
                          const path = `M ${pts.join(" L ")}`;
                          return (
                            <>
                              <path d={path} className="arc-curve__fg-line" stroke={`url(#arc-grad-${p.id})`} />
                              {wps.map((d, i) => {
                                const x = (i / Math.max(1, wps.length - 1)) * W;
                                const y = 60 - (d.y / max) * 50;
                                return <circle key={i} cx={x} cy={y} r="2" className="arc-curve__dot" />;
                              })}
                            </>
                          );
                        })()}
                      </svg>
                    </div>
                  )}
                </div>

                {/* 章节预览 fan（3D 叠层） */}
                {recent.length > 0 && (
                  // 审计 #11 (2026-07-20)：纯视觉装饰，禁止吞掉鼠标事件，
                  // 否则点击预览会冒泡到父级 project card 触发导航。
                  <div
                    className="chapter-fan"
                    aria-hidden="true"
                    style={{ pointerEvents: "none" }}
                  >
                    {recent.map((c, idx) => (
                      <div
                        key={c.id}
                        className="chapter-fan__card"
                        style={{
                          transform: `translateY(${idx * 4}px) scale(${1 - idx * 0.04})`,
                          zIndex: recent.length - idx,
                          opacity: 1 - idx * 0.18,
                        }}
                      >
                        <span className="chapter-fan__card__no">第{c.chapter_no}章</span>
                        <span className="chapter-fan__card__title">{c.title || "（无标题）"}</span>
                        <span className="chapter-fan__card__preview">{c.content_preview}</span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="project-card__foot" style={{ marginTop: 14 }}>
                  {runningBadge(p) || statusBadge(p.status)}
                  <span className="text-faint text-mono">{p.id.slice(0, 8)}</span>
                  {/* 2026-07-25 修复（前端入口缺失）：
                      之前 ready 项目只有一个"打开 N 章"按钮，worldbuild / outline /
                      character card 都没直跳入口，用户反馈"前端里没用大纲没有角色卡"。
                      现在加 3 个并列按钮：世界观/大纲/角色，点哪个跳哪个页。 */}
                  {p.status === "ready" && (
                    <>
                      <button
                        className="btn btn-ghost"
                        style={{ marginLeft: 4, fontSize: 11.5, padding: "2px 8px" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/projects/${p.id}/worldbuild`);
                        }}
                        aria-label={`查看 ${p.title} 世界观`}
                        title="7 段世界观 + 卷级骨架 + 历史时间线"
                      >
                        🌍 世界观
                      </button>
                      <button
                        className="btn btn-ghost"
                        style={{ marginLeft: 4, fontSize: 11.5, padding: "2px 8px" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/projects/${p.id}/outline`);
                        }}
                        aria-label={`查看 ${p.title} 大纲`}
                        title="弧级大纲（点开可生成）"
                      >
                        📜 大纲
                      </button>
                      <button
                        className="btn btn-ghost"
                        style={{ marginLeft: 4, fontSize: 11.5, padding: "2px 8px" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/projects/${p.id}/characters`);
                        }}
                        aria-label={`查看 ${p.title} 角色列表`}
                        title="角色列表 + 点击进 8 段详情"
                      >
                        👤 角色
                      </button>
                    </>
                  )}
                  {recent.length > 0 && (
                    <button
                      className="btn btn-ghost"
                      style={{ marginLeft: 4, fontSize: 12, padding: "2px 10px" }}
                      onClick={(e) => {
                        e.stopPropagation();  // 不触发 project card onClick
                        navigate(`/projects/${p.id}/chapters`);
                      }}
                      aria-label={`查看 ${p.title} 全部 ${chs.length} 章`}
                    >
                      打开 {chs.length} 章
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
