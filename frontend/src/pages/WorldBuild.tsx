import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Project, WorldBuildResult, StageEvent, MapNode, ForeshadowingRow } from "../types";
import { RelationGraph } from "../components/RelationGraph";
import { EmptyTab } from "../components/worldview/EmptyTab";
import { FactionGraph } from "../components/worldview/FactionGraph";
import { WorldviewTab } from "../components/worldview/WorldviewTab";

// 2026-07-25（修 P19 /simplify 极简）：groupMapByLevel 仅 1 处使用（地图 tab），
// ponytail 极简原则 = 抽离需要 ≥ 2 个调用方。inline 在此。
function groupMapByLevel(nodes: MapNode[]): { level: string; nodes: MapNode[] }[] {
  const seen = new Map<string, MapNode[]>();
  for (const n of nodes) {
    const arr = seen.get(n.level) || [];
    arr.push(n);
    seen.set(n.level, arr);
  }
  return Array.from(seen.entries())
    .map(([level, ns]) => ({ level, nodes: ns }))
    .sort((a, b) => a.level.localeCompare(b.level));
}

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

// top-level tabs 扩展为 8 个（合并原「世界立法」6 个 subtab）
// 用户的反馈：世界构建内容太简陋 — 把 6 个 subtab 提升为 top-level 后，
// 每个分类独立 tab，每个 tab 有自己的统计 + 详情视图 + 空状态。
//
// v2：删掉「势力」tab（跟「人物阵营」重复，用户认知里
// 「人物阵营」就是 factions / 阵营，已合并到「人物阵营」tab 的 M04.3）。
// 旧的「势力」tab 路由保留作为 fallback（避免外部链接 404，但内容是 banner）。
const TOP_TABS = [
  "世界观",      // 7 段世界观 + 4 段故事核心 + 历史时间线
  "人物阵营",    // 角色卡 + 关系图 + 关系网 + 势力阵营（factions）
  "地图",        // GIS 层级地图（原 GIS 地图 subtab）
  "力量体系",    // 境界 tier rail（原 力量体系 subtab）
  "货币物权",    // 货币 + 物品（原 货币物权 subtab）
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
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!projectId) return;
    // 加载项目信息。失败时显式设 error 状态，避免 page 永远卡在「加载中…」
    api.getProject(projectId)
      .then((p) => {
        if (!mountedRef.current) return;
        setProject(p);
        if (p.status === "ready") {
          api.getWorldbuildResult(projectId).then((r) => { if (mountedRef.current) setResult(r); }).catch((e) => { if (mountedRef.current) setError(String(e)); });
        }
      })
      .catch((e) => { if (mountedRef.current) setError(String(e)); });
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
    // 清空上一轮 stageStatus：之前只设第一项为 active，老的 done / failure
    // 标志残留 → 用户看到上一轮失败的 stage 仍带"已 done"加勾。
    // （迭代 #54 时有 job_failed 路径不清空，那之后又被绕过了；这里补回)
    setStageStatus({ [firstStage.key]: "active" });
    setResult(null);  // 防止上一轮的 result 在新 build 没完成前仍显示

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
      if (mountedRef.current) setProgress(payload.progress_percent ?? 100);
      es.close();
      if (mountedRef.current) setBuilding(false);
      const fresh = await api.getWorldbuildResult(projectId);
      if (!mountedRef.current) return;
      setResult(fresh);
      setProject((prev) => (prev ? { ...prev, status: "ready" } : prev));
    });

    es.addEventListener("job_failed", (e) => {
      const payload: StageEvent = JSON.parse((e as MessageEvent).data);
      if (mountedRef.current) setError(`生成失败（${payload.stage}）：${payload.error}`);
      es.close();
      if (mountedRef.current) setBuilding(false);
    });

    es.onerror = () => {
      // 连接异常断开（比如后端重启）；不在这里报错刷屏，留给用户重试
      // 显式 close() 防止某些浏览器重连逻辑（即便浏览器本来会自动关闭显式更稳）
      es.close();
      if (mountedRef.current) setBuilding(false);
    };
  }

  // 加载失败时给出明确提示 + 重试入口，避免「加载中…」死循环
  if (!project) {
    if (error) {
      return (
        <div className="card">
          <div className="banner banner-danger" role="alert">{error}</div>
          <p className="text-muted" style={{ marginTop: 12, fontSize: 12.5 }}>
            后端没起来？默认地址 <span className="text-mono">http://localhost:8132</span>
          </p>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: 12 }}
            onClick={() => window.location.reload()}
            aria-label="重试加载世界构建"
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
              type="button"
              className="btn"
              onClick={() => navigate(`/projects/${project.id}/rules`)}
            >
              规则中心
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => navigate(`/projects/${project.id}/chapters`)}
            >
              查看章节
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => navigate(`/projects/${project.id}/bridge`)}
              aria-label="去写作控制台"
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
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleStart}
              aria-label="开始世界构建"
            >
              ✨ 开始构建
            </button>
          )}

          {(building || progress > 0) && (
            <div className="mt-24">
              <div className="progress-track" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
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
          {/* worldbuilding 摘要统计卡 */}
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

          {/* 顶层 8 个 tab（原 3 个 + 6 个 subtab 全部提升） */}
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
              {/* 关系图谱（嵌入 tab 顶部） */}
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
              {/* factions（势力）搬进「人物阵营」tab，让 tab 名实相符。
                  修复前 factions 只在「势力」tab，但用户认知里「人物阵营」= 阵营。
                  关系网 → 势力阵营 自然延续（关系 → 阵营归属）。 */}
              <h3 className="module-heading">
                <span className="module-heading__index">M04.3</span>
                势力阵营
                <span className="module-heading__sub">
                  {result.factions.length} 个阵营
                  {result.factions.length === 0 && " · 尚未生成"}
                </span>
              </h3>
              {result.factions.length === 0 ? (
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
                  {result.characters.length > 0 && result.factions.length > 0 && (
                    <FactionGraph
                      projectId={projectId!}
                      factionNames={result.factions.map((f) => f.name)}
                    />
                  )}
                  <div className="legislation-grid">
                    {result.factions.map((f) => {
                      const dj = (f.detail_json || {}) as Record<string, unknown>;
                      const detail = typeof dj.detail === "string" ? dj.detail : "";
                      const structure = typeof dj.structure === "string" ? dj.structure : "";
                      const goals = typeof dj.goals === "string" ? dj.goals : "";
                      const territory = typeof dj.territory === "string" ? dj.territory : "";
                      const allies = (dj.allies as string[] | undefined) || [];
                      const enemies = (dj.enemies as string[] | undefined) || [];
                      return (
                        <div key={f.id} className="legislation-card">
                          <div className="legislation-card__head">
                            <span className="legislation-card__title">{f.name}</span>
                            <span className="legislation-card__kicker">势力</span>
                          </div>
                          {detail && <span className="legislation-card__desc">{detail}</span>}
                          <div className="legislation-card__chips" style={{ marginTop: 8 }}>
                            {structure && (
                              <span className="legislation-card__chip" title={structure}>
                                结构 · {structure.length > 20 ? structure.slice(0, 20) + "…" : structure}
                              </span>
                            )}
                            {goals && (
                              <span className="legislation-card__chip" title={goals}>
                                目标 · {goals.length > 20 ? goals.slice(0, 20) + "…" : goals}
                              </span>
                            )}
                            {territory && (
                              <span className="legislation-card__chip">地盘 · {territory}</span>
                            )}
                          </div>
                          {(allies.length > 0 || enemies.length > 0) && (
                            <div className="legislation-card__chips" style={{ marginTop: 6 }}>
                              {allies.map((a) => (
                                <span key={a} className="legislation-card__chip" style={{ color: "var(--color-moss)" }}>
                                  盟友 · {a}
                                </span>
                              ))}
                              {enemies.map((e) => (
                                <span key={e} className="legislation-card__chip" style={{ color: "var(--color-stamp)" }}>
                                  敌对 · {e}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
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
                            // tier 结构化展示 hover 详情
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
                    // 结构化 detail_json
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

          {/* 「势力」tab 已合并到「人物阵营」tab（M04.3），此处不再渲染。
              旧路由兼容：topTab 不再含 "势力"，如果有人老链接直跳 default。 */}

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

