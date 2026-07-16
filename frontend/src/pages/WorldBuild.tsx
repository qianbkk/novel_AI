import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Project, WorldBuildResult, StageEvent, MapNode, ForeshadowingRow } from "../types";
import { RelationGraph } from "../components/RelationGraph";

// 阶段清单从后端 GET /worldbuild/stages 动态拉，避免前后端 STAGES 漂移。
// 离线/后端不可达时 fallback 到这份内联默认（同时给首屏立即可渲染的骨架），
// 保证 fetch 失败也不闪屏。
const FALLBACK_STAGES: { key: string; label: string }[] = [
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

// 修订 2026-07-16：top-level tabs 扩展为 8 个（合并原「世界立法」6 个 subtab）
// 用户的反馈：世界构建内容太简陋 — 把 6 个 subtab 提升为 top-level 后，
// 每个分类独立 tab，每个 tab 有自己的统计 + 详情视图 + 空状态。
const TOP_TABS = [
  "世界观",      // 7 段世界观 + 4 段故事核心 + 历史时间线
  "人物阵营",    // 角色卡 + 关系图 + 关系网
  "地图",        // GIS 层级地图（原 GIS 地图 subtab）
  "力量体系",    // 境界 tier rail（原 力量体系 subtab）
  "货币物权",    // 货币 + 物品（原 货币物权 subtab）
  "势力",        // 阵营 + FactionGraph（原 势力 subtab）
  "伏笔",        // 伏笔系统（原 伏笔 subtab）
  "一致性校验",  // 一致性问题（原 一致性校验 subtab）
] as const;
type TopTab = (typeof TOP_TABS)[number];

// 保留类型以便老引用还能编译（虽然不再使用，但避免 import error）
type LegislationTab = "GIS 地图" | "力量体系" | "货币物权" | "势力" | "伏笔" | "一致性校验";
const LEGISLATION_TABS: LegislationTab[] = ["GIS 地图", "力量体系", "货币物权", "势力", "伏笔", "一致性校验"];

export default function WorldBuild() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [stages, setStages] = useState<{ key: string; label: string }[]>(FALLBACK_STAGES);
  const [stageStatus, setStageStatus] = useState<Record<string, StageStatus>>({});
  const [progress, setProgress] = useState(0);
  const [building, setBuilding] = useState(false);
  const [result, setResult] = useState<WorldBuildResult | null>(null);
  const [topTab, setTopTab] = useState<TopTab>("世界观");
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!projectId) return;
    // 加载项目信息。失败时显式设 error 状态，避免 page 永远卡在「加载中…」
    api.getProject(projectId)
      .then((p) => {
        setProject(p);
        if (p.status === "ready") {
          api.getWorldbuildResult(projectId).then(setResult).catch((e) => setError(String(e)));
        }
      })
      .catch((e) => setError(String(e)));
    return () => eventSourceRef.current?.close();
  }, [projectId]);

  // 阶段清单从后端拉（不阻塞首屏 — FALLBACK_STAGES 已就位）
  useEffect(() => {
    let cancelled = false;
    api.listWorldbuildStages()
      .then((r) => {
        // 防御：组件已经卸载（路由切换）/ 已经开始构建（用户点"开始"了）
        // → 不要用后端清单覆盖 stages，避免 SSE 进度条闪烁
        if (cancelled || building) return;
        if (Array.isArray(r.stages) && r.stages.length > 0) {
          setStages(r.stages);
          // 用后端真实清单重置 stageStatus，确保新增 stage 也被覆盖
          setStageStatus((prev) => {
            const next: Record<string, StageStatus> = {};
            for (const s of r.stages) {
              next[s.key] = prev[s.key] ?? "pending";
            }
            return next;
          });
        }
      })
      .catch((err) => {
        // 开发期帮助：后端持续 500 / CORS 不通时静默 fallback 会让 bug 不可见
        // eslint-disable-next-line no-console
        console.warn("[WorldBuild] stages fetch failed, using fallback:", err);
      });
    return () => { cancelled = true; };
  }, [building]);

  async function handleStart() {
    if (!projectId) return;
    if (stages.length === 0) return;  // 安全护栏：FALLBACK_STAGES 应该非空，但万一未来被清空不要炸
    const firstStage = stages[0];
    setError(null);
    setBuilding(true);
    setProgress(0);
    setStageStatus({ [firstStage.key]: "active" });

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
      // 显式 close() 防止某些浏览器重连逻辑（即便浏览器本来会自动关闭显式更稳）
      es.close();
      setBuilding(false);
    };
  }

  // 加载失败时给出明确提示 + 重试入口，避免「加载中…」死循环
  if (!project) {
    if (error) {
      return (
        <div className="card">
          <div className="banner banner-danger">{error}</div>
          <p className="text-muted" style={{ marginTop: 12, fontSize: 12.5 }}>
            后端没起来？默认地址 <span className="text-mono">http://localhost:8132</span>
          </p>
          <button
            className="btn btn-primary"
            style={{ marginTop: 12 }}
            onClick={() => window.location.reload()}
          >
            重试
          </button>
        </div>
      );
    }
    return <p className="loading-text">加载中…</p>;
  }

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
                {stages.map((s) => {
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
          {/* 修订 2026-07-16：worldbuilding 摘要统计卡 */}
          <div className="card worldbuild-summary">
            <div className="worldbuild-summary__grid">
              <div className="worldbuild-summary__stat">
                <span className="worldbuild-summary__num">{result.characters.length}</span>
                <span className="worldbuild-summary__label">角色</span>
              </div>
              <div className="worldbuild-summary__stat">
                <span className="worldbuild-summary__num">{result.factions.length}</span>
                <span className="worldbuild-summary__label">势力</span>
              </div>
              <div className="worldbuild-summary__stat">
                <span className="worldbuild-summary__num">{result.power_systems.reduce((sum, p) => sum + (p.tiers_json?.length || 0), 0)}</span>
                <span className="worldbuild-summary__label">境界层级</span>
              </div>
              <div className="worldbuild-summary__stat">
                <span className="worldbuild-summary__num">{result.map_nodes.length}</span>
                <span className="worldbuild-summary__label">地图节点</span>
              </div>
              <div className="worldbuild-summary__stat">
                <span className="worldbuild-summary__num">{result.foreshadowings.length}</span>
                <span className="worldbuild-summary__label">伏笔</span>
              </div>
              <div className="worldbuild-summary__stat">
                <span className="worldbuild-summary__num">{result.currencies.length}</span>
                <span className="worldbuild-summary__label">货币</span>
              </div>
              <div className="worldbuild-summary__stat">
                <span className="worldbuild-summary__num">{result.relations.length}</span>
                <span className="worldbuild-summary__label">人物关系</span>
              </div>
              <div className={`worldbuild-summary__stat ${result.consistency_warnings.length > 0 ? "is-warn" : ""}`}>
                <span className="worldbuild-summary__num">{result.consistency_warnings.length}</span>
                <span className="worldbuild-summary__label">一致性告警</span>
              </div>
            </div>
          </div>

          {result.consistency_warnings.length > 0 && (
            <div className="banner banner-warn">
              ⚠ 一致性校验发现 {result.consistency_warnings.length} 条待复核（重名/孤儿节点/悬空引用），
              详见「一致性校验」tab。
            </div>
          )}

          {/* 顶层 8 个 tab（修订 2026-07-16：原 3 个 + 6 个 subtab 全部提升） */}
          <div className="tabs tabs--scrollable">
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
            <WorldviewTab result={result} />
          )}

          {/* ===================== 人物阵营 tab ===================== */}
          {topTab === "人物阵营" && (
            <div>
              {/* Phase 6: 关系图谱（嵌入 tab 顶部） */}
              <h3 className="module-heading">
                <span className="module-heading__index">M04</span>
                关系图谱
                <span className="module-heading__sub">主角居中 · 按 role 扇区分布 · 关系边按类型染色</span>
              </h3>
              <RelationGraph
                projectId={projectId!}
                onNodeClick={(cid) => navigate(`/projects/${projectId}/characters/${cid}`)}
              />

              <h3 className="module-heading">
                <span className="module-heading__index">M04.1</span>
                角色动态生命周期
                <span className="module-heading__sub">{result.characters.length} 名角色 · {result.relations.length} 条关系</span>
              </h3>
              <div className="legislation-grid" style={{ marginBottom: 18 }}>
                {result.characters.map((c) => (
                  <div
                    key={c.id}
                    className="legislation-card"
                    style={{ cursor: "pointer" }}
                    onClick={() => navigate(`/projects/${projectId}/characters/${c.id}`)}
                    title="点开查看完整角色卡"
                  >
                    <div className="legislation-card__head">
                      <span className="legislation-card__title">{c.name}</span>
                      <span className="life-dot life-dot--alive">存续</span>
                    </div>
                    <span className="legislation-card__desc">{c.role || "未分配角色"}</span>
                    <div className="legislation-card__chips">
                      <span className="legislation-card__chip">id · {c.id.slice(0, 6)}</span>
                      <span className="legislation-card__chip">→ 查看详情</span>
                    </div>
                  </div>
                ))}
              </div>
              <h3 className="module-heading">
                <span className="module-heading__index">M04.2</span>
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

          {/* ===================== 地图 tab（提升自 GIS 地图） ===================== */}
          {topTab === "地图" && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M02.1</span>
                地理信息系统 · 路径规划
                <span className="module-heading__sub">{result.map_nodes.length} 个子节点 · 世界 → 大陆 → 城市</span>
              </h3>
              {result.map_nodes.length === 0 ? (
                <EmptyTab
                  icon="🗺️"
                  title="还没有地图节点"
                  hint="世界构建未生成地图数据，或项目尚未运行世界构建。"
                  actionLabel="重新运行世界构建"
                  onAction={handleStart}
                />
              ) : (
                groupMapByLevel(result.map_nodes).map((group) => (
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
                ))
              )}
            </div>
          )}

          {/* ===================== 力量体系 tab（提升自 subtab） ===================== */}
          {topTab === "力量体系" && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M02.2</span>
                社会规则 · 力量等级
                <span className="module-heading__sub">突破事件触发阶梯同步 · 鼠标悬停看突破条件</span>
              </h3>
              {result.power_systems.length === 0 ? (
                <EmptyTab
                  icon="⚔️"
                  title="还没有力量体系数据"
                  hint="世界构建未生成 tier 数据。运行世界构建后会自动生成境界 / 层级。"
                  actionLabel="重新运行世界构建"
                  onAction={handleStart}
                />
              ) : (
                result.power_systems.map((p) => (
                    <div className="entity-card" key={p.id}>
                      <div className="entity-card__name">{p.name}</div>
                      <div className="entity-card__desc">{p.description}</div>
                      {p.tiers_json && p.tiers_json.length > 0 && (
                        <div className="tier-rail" style={{ marginTop: 10 }}>
                          {p.tiers_json.map((t, i) => {
                            const reached = i < Math.ceil(p.tiers_json!.length / 2);
                            const isCurrent = i === Math.floor(p.tiers_json!.length / 2);
                            // Phase 7: tier 结构化展示 hover 详情
                            const summary = (t as any).summary as string | undefined;
                            const breakCond = (t as any).break_condition as string | undefined;
                            const cultTime = (t as any).cultivation_time as string | undefined;
                            const hasDetail = !!(summary || breakCond || cultTime);
                            return (
                              <div
                                key={t.level}
                                className={`tier-rail__step ${isCurrent ? "is-current" : reached ? "is-reached" : ""}`}
                                title={
                                  hasDetail
                                    ? `${t.name}\n${summary ? '简介：' + summary : ''}${breakCond ? '\n突破条件：' + breakCond : ''}${cultTime ? '\n修炼时长：' + cultTime : ''}`
                                    : t.name
                                }
                                style={{ cursor: hasDetail ? "help" : "default" }}
                              >
                                {t.name}
                                {hasDetail && (
                                  <div className="tier-rail__detail">
                                    {summary && <p className="tier-detail-row"><strong>简介：</strong>{summary}</p>}
                                    {breakCond && <p className="tier-detail-row"><strong>突破：</strong>{breakCond}</p>}
                                    {cultTime && <p className="tier-detail-row"><strong>时长：</strong>{cultTime}</p>}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  ))
              )}
            </div>
          )}

          {/* ===================== 货币物权 tab（提升自 subtab） ===================== */}
          {topTab === "货币物权" && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M02.3</span>
                货币与物权追踪
                <span className="module-heading__sub">支持汇率核算 · 物品流转自动同步</span>
              </h3>
              {result.currencies.length === 0 ? (
                <EmptyTab
                  icon="💰"
                  title="还没有货币 / 物权数据"
                  hint="世界构建未生成货币 / 物权数据。"
                  actionLabel="重新运行世界构建"
                  onAction={handleStart}
                />
              ) : (
                <div className="legislation-grid">
                  {result.currencies.map((c) => {
                    // Phase 7: 结构化 detail_json
                    const dj = (c.detail_json || {}) as Record<string, unknown>;
                    const detail = dj.detail as string | undefined;
                    const exchange = dj.exchange_rate as string | undefined;
                    const issuers = (dj.issuers as string[] | undefined) || [];
                    const scope = dj.scope as string | undefined;
                    const hasRich = !!(exchange || issuers.length || scope);
                    return (
                      <div key={c.id} className="legislation-card">
                        <div className="legislation-card__head">
                          <span className="legislation-card__title">{c.name}</span>
                          <span className="legislation-card__kicker">货币</span>
                        </div>
                        <span className="legislation-card__desc">{(() => {
                          if (typeof detail === "string" && detail) return detail;
                          const psName = typeof dj.power_system_name === "string" ? dj.power_system_name : "";
                          if (psName) return `来源：${psName}`;
                          const src = typeof dj.source === "string" ? dj.source : "";
                          if (src) return `来源：${src}`;
                          try {
                            return JSON.stringify(dj) || "—";
                          } catch {
                            return "—";
                          }
                        })()}</span>
                        {hasRich && (
                          <div className="legislation-card__chips" style={{ marginTop: 8 }}>
                            {exchange && (
                              <span className="legislation-card__chip">汇率 · {exchange}</span>
                            )}
                            {issuers.length > 0 && (
                              <span className="legislation-card__chip">发行 · {issuers.join(" / ")}</span>
                            )}
                            {scope && (
                              <span className="legislation-card__chip">范围 · {scope}</span>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ===================== 势力 tab（提升自 subtab，Issue #3 修复） ===================== */}
          {topTab === "势力" && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M02.4</span>
                势力阵营
                <span className="module-heading__sub">{result.factions.length} 个阵营 · {result.relations.length} 条关系</span>
              </h3>
              {result.factions.length === 0 ? (
                /* 修订 2026-07-16：factions 空状态 + 重新构建 CTA（Issue #3） */
                <EmptyTab
                  icon="⚔️"
                  title="还没有阵营数据"
                  hint={
                    result.characters.length === 0
                      ? "需要先运行「世界构建」生成角色和势力基础数据。"
                      : `已有 ${result.characters.length} 个角色，但势力生成失败。点击下方按钮重新运行。`
                  }
                  actionLabel="重新运行世界构建"
                  onAction={handleStart}
                />
              ) : (
                <>
                  {result.characters.length > 0 && (
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
                        <span className="legislation-card__desc">{(() => {
                              const fDetail = typeof f.detail_json?.detail === "string" ? f.detail_json.detail : "";
                              if (fDetail) return fDetail;
                              const fRaw = f.detail_json?.raw;
                              if (fRaw) return String(fRaw);
                              try {
                                return JSON.stringify(f.detail_json || {}) || "—";
                              } catch {
                                return "—";
                              }
                            })() || "—"}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {/* ===================== 伏笔 tab（提升自 subtab） ===================== */}
          {topTab === "伏笔" && (
            <div>
              <h3 className="module-heading">
                <span className="module-heading__index">M03</span>
                伏笔系统
                <span className="module-heading__sub">回收提醒 · 重要性分级 · 状态流转</span>
              </h3>
              {result.foreshadowings.length === 0 ? (
                <EmptyTab
                  icon="🎯"
                  title="还没有伏笔"
                  hint="伏笔由世界构建生成或写作时由 tracker 自动追加。"
                  actionLabel="重新运行世界构建"
                  onAction={handleStart}
                />
              ) : (
                result.foreshadowings.map((f) => {
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
                })
              )}
            </div>
          )}

          {/* ===================== 一致性校验 tab（提升自 subtab） ===================== */}
          {topTab === "一致性校验" && (
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
        </>
      )}
    </div>
  );
}

// 修订 2026-07-16：通用空状态组件 — 当 tab 数据为空时显示 + 提供「重新构建」CTA
function EmptyTab({
  icon, title, hint, actionLabel, onAction,
}: {
  icon: string;
  title: string;
  hint: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="empty-state empty-state--with-action">
      <div className="empty-state__icon" style={{ fontSize: 36 }}>{icon}</div>
      <div className="empty-state__title">{title}</div>
      <div className="empty-state__hint" style={{ maxWidth: 420, textAlign: "center" }}>{hint}</div>
      {actionLabel && onAction && (
        <button className="btn btn-primary" onClick={onAction} style={{ marginTop: 16 }}>
          {actionLabel}
        </button>
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

// ════════════════════════════════════════════════════════════════════════════
// Phase 5：世界观 tab 7 段结构化 + 历史时间线
// ════════════════════════════════════════════════════════════════════════════
function WorldviewTab({ result }: { result: WorldBuildResult }) {
  const rich = (result as any).worldview_rich as null | Record<string, string>;
  const storyCore = (result as any).story_core_struct as null | Record<string, string>;
  const timeline = (result as any).history_timeline as null | Array<{ era: string; event: string; impact: string }>;
  const ws = result.world_setting;

  return (
    <div>
      {/* 故事核心 */}
      <h3 className="module-heading">
        <span className="module-heading__index">M03</span>
        叙事工程 · 故事核心
        <span className="module-heading__sub">主线记忆防线 L1</span>
      </h3>

      {/* Phase 5: 故事核心 4 段（新结构） */}
      {storyCore ? (
        <>
          {storyCore.goal && (
            <div className="entity-card">
              <div className="entity-card__name">目标</div>
              <div className="entity-card__desc">{storyCore.goal}</div>
            </div>
          )}
          {storyCore.conflict && (
            <div className="entity-card">
              <div className="entity-card__name">冲突</div>
              <div className="entity-card__desc">{storyCore.conflict}</div>
            </div>
          )}
          {storyCore.theme && (
            <div className="entity-card">
              <div className="entity-card__name">主题</div>
              <div className="entity-card__desc">{storyCore.theme}</div>
            </div>
          )}
          {storyCore.hook && (
            <div className="entity-card">
              <div className="entity-card__name">开篇钩子</div>
              <div className="entity-card__desc">{storyCore.hook}</div>
            </div>
          )}
        </>
      ) : ws?.story_core ? (
        /* 老项目 fallback */
        <div className="entity-card">
          <div className="entity-card__name">故事核心</div>
          <div className="entity-card__desc">{ws.story_core}</div>
        </div>
      ) : null}

      {/* Phase 5: 7 段世界观（新结构） */}
      {rich ? (
        <div style={{ marginTop: 18 }}>
          <WorldviewSection title="宇宙观 / 天地法则" text={rich.cosmos} idx="M03.1" />
          <WorldviewSection title="地理总览"           text={rich.geography} idx="M03.2" />
          <WorldviewSection title="历史概述"           text={rich.history} idx="M03.3" />
          <WorldviewSection title="社会制度"           text={rich.society} idx="M03.4" />
          <WorldviewSection title="科技/修炼体系"     text={rich.technology} idx="M03.5" />
          <WorldviewSection title="种族/族群"         text={rich.races} idx="M03.6" />
          <WorldviewSection title="风土人情"           text={rich.customs} idx="M03.7" />
        </div>
      ) : ws?.world_view ? (
        /* 老项目 fallback */
        <div className="entity-card" style={{ marginTop: 18 }}>
          <div className="entity-card__name">世界观设定</div>
          <div className="entity-card__desc">{ws.world_view}</div>
        </div>
      ) : null}

      {/* 老项目 banner 提示升级 */}
      {!rich && !ws?.world_view && (
        <div className="banner banner-warn" style={{ marginTop: 14 }}>
          ⚠ 该项目的世界观尚未升级为结构化数据（老版本 worldbuild）。
          请重新跑 worldbuild 升级为 7 段结构化。
        </div>
      )}

      {/* Phase 5: 历史时间线 */}
      {timeline && timeline.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <h3 className="module-heading">
            <span className="module-heading__index">M03.8</span>
            历史时间线
            <span className="module-heading__sub">{timeline.length} 个大事件</span>
          </h3>
          <div className="history-timeline">
            {timeline.map((node, i) => (
              <div key={i} className="history-timeline__node">
                <span className="history-timeline__era">{node.era}</span>
                <div className="history-timeline__event">{node.event}</div>
                <div className="history-timeline__impact">{node.impact}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 卷级骨架（保留） */}
      {ws?.plot_skeleton_json && ws.plot_skeleton_json.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <h3 className="module-heading">
            <span className="module-heading__index">M03.9</span>
            卷级骨架
            <span className="module-heading__sub">{ws.plot_skeleton_json.length} 卷</span>
          </h3>
          {ws.plot_skeleton_json.map((v, i) => (
            <div className="entity-card" key={i}>
              <div className="entity-card__name">
                <span className="last-chapter-line__no" style={{ marginRight: 8 }}>卷 {i + 1}</span>
                {v.title}
              </div>
              <div className="entity-card__desc">{v.summary}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function WorldviewSection({
  title, text, idx,
}: { title: string; text: string; idx: string }) {
  return (
    <div className="entity-card" style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="mono" style={{ color: "var(--color-accent-strong)", fontSize: 11 }}>
          {idx}
        </span>
        <div className="entity-card__name">{title}</div>
      </div>
      <div className="entity-card__desc">{text}</div>
    </div>
  );
}
