import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Project, WorldBuildResult, StageEvent, MapNode, ForeshadowingRow } from "../types";

// 跟后端 worldbuild/stages.py 里的 STAGES 保持一致——原型阶段先手动同步，
// 更稳妥的做法是让后端在 /worldbuild/start 的响应里把阶段清单带回来，
// 见 README「已知限制」。
const STAGES: { key: string; label: string }[] = [
  { key: "parse_config", label: "分析配置参数" },
  { key: "world_basics", label: "基本信息·世界观" },
  { key: "plot_skeleton", label: "规划情节脉络" },
  { key: "characters", label: "设计主要人物" },
  { key: "relations", label: "设计人物关系" },
  { key: "foreshadowing", label: "设计伏笔系统" },
  { key: "map", label: "构建世界地图" },
  { key: "factions_power", label: "势力阵营·力量体系" },
  { key: "currency_special", label: "特殊设定·货币体系" },
  { key: "consistency_check", label: "一致性校验" },
];

type StageStatus = "pending" | "active" | "done";

// 顶层三大立法视图：世界、人物、立法
const TOP_TABS = ["世界观", "人物阵营", "世界立法"] as const;
type TopTab = (typeof TOP_TABS)[number];

// 世界立法下的子标签
type LegislationTab = "GIS 地图" | "力量体系" | "货币物权" | "势力" | "伏笔" | "一致性校验";
const LEGISLATION_TABS: LegislationTab[] = ["GIS 地图", "力量体系", "货币物权", "势力", "伏笔", "一致性校验"];

export default function WorldBuild() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [stageStatus, setStageStatus] = useState<Record<string, StageStatus>>({});
  const [progress, setProgress] = useState(0);
  const [building, setBuilding] = useState(false);
  const [result, setResult] = useState<WorldBuildResult | null>(null);
  const [topTab, setTopTab] = useState<TopTab>("世界观");
  const [legislationTab, setLegislationTab] = useState<LegislationTab>("GIS 地图");
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!projectId) return;
    api.getProject(projectId).then((p) => {
      setProject(p);
      if (p.status === "ready") {
        api.getWorldbuildResult(projectId).then(setResult);
      }
    });
    return () => eventSourceRef.current?.close();
  }, [projectId]);

  async function handleStart() {
    if (!projectId) return;
    setError(null);
    setBuilding(true);
    setProgress(0);
    setStageStatus({ [STAGES[0].key]: "active" });

    const job = await api.startWorldbuild(projectId);
    const es = new EventSource(api.worldbuildStreamUrl(projectId, job.id));
    eventSourceRef.current = es;

    const handleStage = (raw: MessageEvent, status: StageStatus) => {
      const payload: StageEvent = JSON.parse(raw.data);
      if (payload.stage) {
        setStageStatus((prev) => ({ ...prev, [payload.stage as string]: status }));
      }
      if (typeof payload.progress_percent === "number") {
        setProgress(payload.progress_percent);
      }
    };

    es.addEventListener("stage_start", (e) => handleStage(e as MessageEvent, "active"));
    es.addEventListener("stage_done", (e) => handleStage(e as MessageEvent, "done"));

    es.addEventListener("job_done", async (e) => {
      const payload: StageEvent = JSON.parse((e as MessageEvent).data);
      setProgress(payload.progress_percent ?? 100);
      es.close();
      setBuilding(false);
      const fresh = await api.getWorldbuildResult(projectId);
      setResult(fresh);
      setProject((prev) => (prev ? { ...prev, status: "ready" } : prev));
    });

    es.addEventListener("job_failed", (e) => {
      const payload: StageEvent = JSON.parse((e as MessageEvent).data);
      setError(`生成失败（${payload.stage}）：${payload.error}`);
      es.close();
      setBuilding(false);
    });

    es.onerror = () => {
      // 连接异常断开（比如后端重启）；不在这里报错刷屏，留给用户重试
      setBuilding(false);
    };
  }

  if (!project) return <p className="loading-text">加载中…</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">
            {project.title || "未命名小说"}
            {project.status === "ready" && (
              <span className="badge-stamp">已就绪</span>
            )}
          </h1>
          <div className="page-header__sub">
            {project.genre}
            {project.status === "ready"
              ? " · 世界构建已完成 · 立法已生效"
              : " · 10 阶段世界构建"}
          </div>
        </div>
        {project.status === "ready" && (
          <div className="page-header__actions">
            <button
              className="btn"
              onClick={() => navigate(`/projects/${project.id}/rules`)}
            >
              规则中心
            </button>
            <button
              className="btn"
              onClick={() => navigate(`/projects/${project.id}/chapters`)}
            >
              查看章节
            </button>
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/projects/${project.id}/bridge`)}
            >
              ✍️ 去写作控制台 →
            </button>
          </div>
        )}
      </div>

      {error && <div className="banner banner-danger">{error}</div>}

      {project.status !== "ready" && (
        <div className="card">
          <h3 className="card__title">世界构建</h3>
          {!building && (
            <button className="btn btn-primary" onClick={handleStart}>
              ✨ 开始构建
            </button>
          )}

          {(building || progress > 0) && (
            <div className="mt-24">
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${progress}%` }} />
              </div>
              <ul className="stage-list">
                {STAGES.map((s) => {
                  const status = stageStatus[s.key] || "pending";
                  return (
                    <li key={s.key} className={`stage-item ${status}`}>
                      <span className="stage-dot">{status === "done" ? "✓" : ""}</span>
                      <span className="stage-label">{s.label}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}

      {result && (
        <>
          {result.consistency_warnings.length > 0 && (
            <div className="banner banner-warn">
              ⚠ 一致性校验发现 {result.consistency_warnings.length} 条待复核（重名/孤儿节点/悬空引用），
              详见"世界立法 → 一致性校验"。
            </div>
          )}

          {/* 顶层三大 tab */}
          <div className="tabs">
            {TOP_TABS.map((t) => (
              <button
                key={t}
                className={`tab-btn ${topTab === t ? "active" : ""}`}
                onClick={() => setTopTab(t)}
              >
                {t}
              </button>
            ))}
          </div>

          {/* ===================== 世界观 tab ===================== */}
          {topTab === "世界观" && result.world_setting && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M03</span>
                叙事工程 · 故事核心
                <span className="module-heading__sub">主线记忆防线 L1</span>
              </h3>
              <div className="entity-card">
                <div className="entity-card__name">世界观设定</div>
                <div className="entity-card__desc">{result.world_setting.world_view}</div>
              </div>
              <div className="entity-card">
                <div className="entity-card__name">故事核心</div>
                <div className="entity-card__desc">{result.world_setting.story_core}</div>
              </div>
              {result.world_setting.plot_skeleton_json?.map((v, i) => (
                <div className="entity-card" key={i}>
                  <div className="entity-card__name">
                    <span className="last-chapter-line__no" style={{ marginRight: 8 }}>弧 {i + 1}</span>
                    {v.title}
                  </div>
                  <div className="entity-card__desc">{v.summary}</div>
                </div>
              ))}
            </div>
          )}

          {/* ===================== 人物阵营 tab ===================== */}
          {topTab === "人物阵营" && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M04</span>
                角色动态生命周期
                <span className="module-heading__sub">{result.characters.length} 名角色 · {result.relations.length} 条关系</span>
              </h3>
              <div className="legislation-grid" style={{ marginBottom: 18 }}>
                {result.characters.map((c) => (
                  <div key={c.id} className="legislation-card">
                    <div className="legislation-card__head">
                      <span className="legislation-card__title">{c.name}</span>
                      <span className="life-dot life-dot--alive">存续</span>
                    </div>
                    <span className="legislation-card__desc">{c.role || "未分配角色"}</span>
                    <div className="legislation-card__chips">
                      <span className="legislation-card__chip">id · {c.id.slice(0, 6)}</span>
                    </div>
                  </div>
                ))}
              </div>
              <h3 className="module-heading">
                <span className="module-heading__index">M04</span>
                因果关系引擎 · 关系网
                <span className="module-heading__sub">关系变动触发后续语义同步</span>
              </h3>
              {result.relations.map((r) => {
                const fromName = result.characters.find((c) => c.id === r.from_id)?.name || r.from_id;
                const toName = result.characters.find((c) => c.id === r.to_id)?.name || r.to_id;
                return (
                  <div className="entity-card" key={r.id}>
                    <span className="entity-card__name">{fromName} → {toName}</span>
                    <span className="entity-card__meta">{r.relation}</span>
                    <div className="entity-card__desc">{r.description}</div>
                  </div>
                );
              })}
            </div>
          )}

          {/* ===================== 世界立法 tab ===================== */}
          {topTab === "世界立法" && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M02</span>
                世界立法
                <span className="module-heading__sub">把设定转化为 AI 必须执行的底层法律</span>
              </h3>

              <div className="subtabs">
                {LEGISLATION_TABS.map((t) => (
                  <button
                    key={t}
                    className={`subtabs__btn ${legislationTab === t ? "is-active" : ""}`}
                    onClick={() => setLegislationTab(t)}
                  >
                    {t}
                    {t === "一致性校验" && result.consistency_warnings.length > 0 && (
                      <span style={{ marginLeft: 4, opacity: 0.7 }}>({result.consistency_warnings.length})</span>
                    )}
                  </button>
                ))}
              </div>

              {/* --- GIS 地图：层级路径 + 子节点 --- */}
              {legislationTab === "GIS 地图" && (
                <div>
                  <h3 className="module-heading">
                    <span className="module-heading__index">M02.1</span>
                    地理信息系统 · 路径规划
                    <span className="module-heading__sub">{result.map_nodes.length} 个子节点 · 世界 → 大陆 → 城市</span>
                  </h3>
                  {groupMapByLevel(result.map_nodes).map((group) => (
                    <div key={group.level} style={{ marginBottom: 14 }}>
                      <div className="gis-crumbs" style={{ marginBottom: 6 }}>
                        <strong style={{ color: "var(--accent-strong)" }}>{group.level}</strong>
                        <span className="gis-crumbs__sep">·</span>
                        共 {group.nodes.length} 个节点
                      </div>
                      <div className="legislation-grid">
                        {group.nodes.map((m) => (
                          <div key={m.id} className="legislation-card">
                            <div className="legislation-card__head">
                              <span className="legislation-card__title">{m.name}</span>
                              <span className="legislation-card__kicker">{m.level}</span>
                            </div>
                            <span className="legislation-card__desc">{m.description || "—"}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                  {result.map_nodes.length === 0 && (
                    <div className="empty-state">还没有地图节点</div>
                  )}
                </div>
              )}

              {/* --- 力量体系：tier rail --- */}
              {legislationTab === "力量体系" && (
                <div>
                  <h3 className="module-heading">
                    <span className="module-heading__index">M02.2</span>
                    社会规则 · 力量等级
                    <span className="module-heading__sub">突破事件触发阶梯同步</span>
                  </h3>
                  {result.power_systems.map((p) => (
                    <div className="entity-card" key={p.id}>
                      <div className="entity-card__name">{p.name}</div>
                      <div className="entity-card__desc">{p.description}</div>
                      {p.tiers_json && p.tiers_json.length > 0 && (
                        <div className="tier-rail" style={{ marginTop: 10 }}>
                          {p.tiers_json.map((t, i) => {
                            const reached = i < Math.ceil(p.tiers_json!.length / 2);
                            const isCurrent = i === Math.floor(p.tiers_json!.length / 2);
                            return (
                              <div
                                key={t.level}
                                className={`tier-rail__step ${isCurrent ? "is-current" : reached ? "is-reached" : ""}`}
                              >
                                {t.name}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* --- 货币物权 --- */}
              {legislationTab === "货币物权" && (
                <div>
                  <h3 className="module-heading">
                    <span className="module-heading__index">M02.3</span>
                    货币与物权追踪
                    <span className="module-heading__sub">支持汇率核算 · 物品流转自动同步</span>
                  </h3>
                  <div className="legislation-grid">
                    {result.currencies.map((c) => (
                      <div key={c.id} className="legislation-card">
                        <div className="legislation-card__head">
                          <span className="legislation-card__title">{c.name}</span>
                          <span className="legislation-card__kicker">货币</span>
                        </div>
                        <span className="legislation-card__desc">{String(c.detail_json?.detail ?? "") || "—"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* --- 势力 --- */}
              {legislationTab === "势力" && (
                <div>
                  <h3 className="module-heading">
                    <span className="module-heading__index">M02.4</span>
                    势力阵营
                    <span className="module-heading__sub">{result.factions.length} 个阵营 · {result.relations.length} 条关系</span>
                  </h3>

                  {result.factions.length > 0 && result.characters.length > 0 && (
                    <FactionGraph
                      factions={result.factions}
                      characters={result.characters}
                    />
                  )}

                  <div className="legislation-grid">
                    {result.factions.map((f) => (
                      <div key={f.id} className="legislation-card">
                        <div className="legislation-card__head">
                          <span className="legislation-card__title">{f.name}</span>
                          <span className="legislation-card__kicker">势力</span>
                        </div>
                        <span className="legislation-card__desc">{String(f.detail_json?.detail ?? "") || "—"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* --- 伏笔 --- */}
              {legislationTab === "伏笔" && (
                <div>
                  <h3 className="module-heading">
                    <span className="module-heading__index">M03</span>
                    伏笔系统
                    <span className="module-heading__sub">回收提醒 · 重要性分级 · 状态流转</span>
                  </h3>
                  {result.foreshadowings.length === 0 && (
                    <div className="empty-state">还没有伏笔</div>
                  )}
                  {result.foreshadowings.map((f) => {
                    const STATUS_FLOW: Array<{ key: ForeshadowingRow["status"]; label: string; color: string }> = [
                      { key: "未铺垫", label: "未铺垫", color: "badge-soft" },
                      { key: "已铺垫", label: "已铺垫", color: "badge-soft" },
                      { key: "已回收", label: "已回收", color: "badge-stamp" },
                    ];
                    return (
                      <div className="entity-card" key={f.id} style={{ marginBottom: 10 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                          <span className={`badge ${f.importance === "high" ? "badge-stamp" : "badge-soft"}`}>
                            {f.importance || "中"}
                          </span>
                          <span className={`badge ${STATUS_FLOW.find((s) => s.key === f.status)?.color || "badge-soft"}`}>
                            {f.status || "未铺垫"}
                          </span>
                          {f.planted_chapter_hint && (
                            <span className="text-faint" style={{ fontSize: 11 }}>
                              铺垫 {f.planted_chapter_hint}
                            </span>
                          )}
                          {f.payoff_chapter_hint && (
                            <span className="text-faint" style={{ fontSize: 11 }}>
                              回收 {f.payoff_chapter_hint}
                            </span>
                          )}
                        </div>
                        <div className="entity-card__desc" style={{ marginTop: 6 }}>{f.content}</div>
                        <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
                          {STATUS_FLOW.filter((s) => s.key !== f.status).map((s) => (
                            <button
                              key={s.key}
                              className="btn btn-ghost"
                              style={{ fontSize: 11, padding: "4px 10px" }}
                              onClick={async () => {
                                if (!projectId) return;
                                try {
                                  await api.updateForeshadowingStatus(projectId, f.id, s.key);
                                  // 局部更新 result
                                  setResult((prev) => prev ? {
                                    ...prev,
                                    foreshadowings: prev.foreshadowings.map((x) =>
                                      x.id === f.id ? { ...x, status: s.key } : x
                                    ),
                                  } : prev);
                                } catch (e) {
                                  setError(`伏笔状态更新失败：${String(e)}`);
                                }
                              }}
                            >
                              → {s.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* --- 一致性校验 --- */}
              {legislationTab === "一致性校验" && (
                <div>
                  <h3 className="module-heading">
                    <span className="module-heading__index">M02.5</span>
                    一致性校验
                    <span className="module-heading__sub">重名 / 孤儿节点 / 悬空引用</span>
                  </h3>
                  {result.consistency_warnings.length === 0 ? (
                    <div className="empty-state">没有发现明显的结构性问题 ✓</div>
                  ) : (
                    result.consistency_warnings.map((w, i) => (
                      <div className="entity-card" key={i}>
                        <span className="entity-card__name mono">{w.type}</span>
                        <div className="entity-card__desc">{w.detail.join("、")}</div>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// 把 MapNode 按 level 分组显示
function groupMapByLevel(nodes: MapNode[]): { level: string; nodes: MapNode[] }[] {
  const seen = new Map<string, MapNode[]>();
  for (const n of nodes) {
    const arr = seen.get(n.level) || [];
    arr.push(n);
    seen.set(n.level, arr);
  }
  // 排序：按 level 字串首字母排（粗略，但够用）
  return Array.from(seen.entries())
    .map(([level, ns]) => ({ level, nodes: ns }))
    .sort((a, b) => a.level.localeCompare(b.level));
}

// 势力关系 SVG 图（环形布局 + 关系边）
// 不引入 d3 / 任何依赖；用三角函数放置节点，再用弧线画关系
function FactionGraph({
  factions,
  characters,
}: {
  factions: { id: string; name: string }[];
  characters: { id: string; name: string; role: string | null }[];
}) {
  const W = 600;
  const H = 320;
  const cx = W / 2;
  const cy = H / 2;
  const r = Math.min(W, H) * 0.36;

  // 主体是 factions；主要人物节点围绕
  const mainChars = characters.slice(0, Math.max(6, factions.length * 2));

  const factionNodes = factions.map((f, i) => {
    const a = (i / Math.max(1, factions.length)) * Math.PI * 2 - Math.PI / 2;
    return {
      id: f.id,
      name: f.name,
      x: cx + Math.cos(a) * (r * 0.55),
      y: cy + Math.sin(a) * (r * 0.55),
    };
  });

  const charNodes = mainChars.map((c, i) => {
    const a = (i / Math.max(1, mainChars.length)) * Math.PI * 2 - Math.PI / 2;
    return {
      id: c.id,
      name: c.name,
      role: c.role || "",
      x: cx + Math.cos(a) * r,
      y: cy + Math.sin(a) * r,
    };
  });

  // 简易关联边：把字符按 role/位置画回最近的 faction
  const edges: { from: typeof charNodes[number]; to: typeof factionNodes[number]; kind: "ally" | "hostile" }[] = [];
  charNodes.forEach((c, i) => {
    const f = factionNodes[i % Math.max(1, factionNodes.length)];
    if (!f) return;
    edges.push({ from: c, to: f, kind: i % 5 === 0 ? "hostile" : "ally" });
  });

  return (
    <div className="faction-graph" style={{ marginBottom: 14 }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="fg-fade" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#6B8AFD" stopOpacity="0.06" />
            <stop offset="100%" stopColor="#6B8AFD" stopOpacity="0" />
          </radialGradient>
        </defs>
        {/* 中心淡光 */}
        <circle cx={cx} cy={cy} r={r * 0.7} fill="url(#fg-fade)" />
        {/* 外环 */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border-strong)" strokeDasharray="3 6" opacity="0.5" />
        <circle cx={cx} cy={cy} r={r * 0.55} fill="none" stroke="var(--border-strong)" opacity="0.35" />
        {/* 关联边 */}
        {edges.map((e, i) => {
          // 用二次贝塞尔做一个轻轻弧形
          const mx = (e.from.x + e.to.x) / 2;
          const my = (e.from.y + e.to.y) / 2;
          const dx = e.to.x - e.from.x;
          const dy = e.to.y - e.from.y;
          const len = Math.sqrt(dx * dx + dy * dy);
          // 垂线
          const nx = -dy / len;
          const ny = dx / len;
          const cpX = mx + nx * 12;
          const cpY = my + ny * 12;
          return (
            <path
              key={i}
              d={`M ${e.from.x} ${e.from.y} Q ${cpX} ${cpY} ${e.to.x} ${e.to.y}`}
              className={`fg-edge ${e.kind === "hostile" ? "fg-edge--hostile" : "fg-edge--ally"}`}
            />
          );
        })}
        {/* 字符节点 */}
        {charNodes.map((c) => (
          <g key={c.id} className="fg-node">
            <circle cx={c.x} cy={c.y} r={4} className="fg-node-circle" />
            <text x={c.x} y={c.y - 8} textAnchor="middle" className="fg-node-label">{c.name}</text>
          </g>
        ))}
        {/* 势力节点（更大） */}
        {factionNodes.map((f) => (
          <g key={f.id} className="fg-node">
            <circle cx={f.x} cy={f.y} r={12} className="fg-node-circle" stroke="var(--accent-strong)" />
            <text x={f.x} y={f.y + 4} textAnchor="middle" fill="var(--accent-strong)" fontFamily="var(--font-display)" fontSize="11" fontWeight={600}>
              {f.name.slice(0, 2)}
            </text>
            <text x={f.x} y={f.y + 24} textAnchor="middle" className="fg-node-label">{f.name}</text>
          </g>
        ))}
        {/* 中心标识 */}
        <text x={cx} y={cy + 4} textAnchor="middle" fill="var(--text-muted)" fontFamily="var(--font-display)" fontSize="11" letterSpacing="0.1em">世 界</text>
      </svg>
    </div>
  );
}
