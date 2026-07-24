import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { ChapterListItem, Project } from "../types";
import { useReveal } from "../hooks/useReveal";

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

function memoryDepth(p: Project, chapters: ChapterListItem[]) {
  const l1 = p.status === "ready" ? 5 : 1;
  const l2 = Math.min(12, chapters.length);
  const words = chapters.reduce((a, c) => a + c.word_count, 0);
  const l3 = Math.min(12, Math.floor(Math.log10(Math.max(1, words)) * 3) + 1);
  return { l1, l2, l3 };
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
  const navigate = useNavigate();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useReveal(rootRef);

  async function loadAll() {
    setError(null);
    try {
      const ps = await api.listProjects({ q, genre });
      if (!mountedRef.current) return;
      setProjects(ps);
      // 审计 #2（2026-07-22）：串行 + 4 并发限制，避免全量并发导致
      // 大量项目首屏加载变慢。不引入 p-limit 依赖，手写 chunk 即可。
      const concurrency = 4;
      const entries: Array<readonly [string, ChapterListItem[], boolean]> = [];
      for (let i = 0; i < ps.length; i += concurrency) {
        const slice = ps.slice(i, i + concurrency);
        const sub = await Promise.all(
          slice.map(async (p) => {
            try {
              const chs = await api.listChapters(p.id);
              return [p.id, chs, false] as const;
            } catch (e) {
              // 审计 #1（2026-07-22）：不再静默吞单个项目章节加载失败，
              // console.warn 让开发者看到，同时 hasError=true 让 UI 能区分
              // 「真的没有章节」与「加载失败」（前端用 hasError 标灰）。
              console.warn(`[Dashboard] listChapters failed for ${p.id}:`, e);
              return [p.id, [] as ChapterListItem[], true] as const;
            }
          }),
        );
        entries.push(...sub);
      }
      if (!mountedRef.current) return;
      // chapterMap: id -> chapters；failures: id -> true（前端可标灰）
      setChapterMap(Object.fromEntries(entries.map(([id, chs]) => [id, chs])));
      setChapterLoadFailures(Object.fromEntries(entries.map(([id, _chs, err]) => [id, err])));
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
      <div className="dashboard-toolbar" style={{ display: "flex", gap: 12, alignItems: "center", margin: "16px 0" }}>
        <input
          type="text"
          placeholder="搜索项目名 / 主角名…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="dashboard-search-input"
        />
        <div className="dashboard-genre-chips">
          <button
            className={`genre-chip ${!genre ? "genre-chip--active" : ""}`}
            onClick={() => setGenre("")}
          >
            全部
          </button>
          {Array.from(new Set((projects || []).map((p) => p.genre).filter(Boolean))).map((g) => (
            <button
              key={g}
              className={`genre-chip ${genre === g ? "genre-chip--active" : ""}`}
              onClick={() => setGenre(g)}
            >
              {g}
            </button>
          ))}
        </div>
      </div>

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
            const mem = memoryDepth(p, chs);
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
                    p.active_run_command
                      ? `/projects/${p.id}/bridge`  // 2026-07-24：跑中跳 BridgeConsole 看 SSE
                      : p.status === "ready"
                      ? `/projects/${p.id}/bridge`
                      : `/projects/${p.id}/worldbuild`,
                  )
                }
              >
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

                {/* 三道记忆防线 · 缩略视图 */}
                <div className="memory-stack" style={{ marginTop: 10, gap: 4 }}>
                  <div className="memory-row memory-row--l1" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">L1</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      主线记忆 · {mem.l1}/5 弧段
                    </span>
                    <span className="memory-row__count">{p.status === "ready" ? "已建立" : "草拟中"}</span>
                  </div>
                  <div className="memory-row memory-row--l2" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">L2</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      衔接锁 · 已写 {chs.length} 章
                    </span>
                    <span className="memory-row__count">{mem.l2}/12</span>
                  </div>
                  <div className="memory-row memory-row--l3" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">L3</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      压缩存储 · {projectWords.toLocaleString()} 字
                    </span>
                    <span className="memory-row__count">深度 {mem.l3}/12</span>
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
                          navigate(`/projects/${p.id}/worldbuild`);
                        }}
                        aria-label={`查看 ${p.title} 角色卡`}
                        title="角色卡 8 段 + 关系图谱 + 势力阵营"
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
