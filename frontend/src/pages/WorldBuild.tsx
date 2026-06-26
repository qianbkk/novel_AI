import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Project, WorldBuildResult, StageEvent } from "../types";

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

const TABS = ["世界观", "人物", "人物关系", "势力", "地图", "力量体系", "货币", "伏笔", "一致性校验"] as const;

export default function WorldBuild() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [stageStatus, setStageStatus] = useState<Record<string, StageStatus>>({});
  const [progress, setProgress] = useState(0);
  const [building, setBuilding] = useState(false);
  const [result, setResult] = useState<WorldBuildResult | null>(null);
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]>("世界观");
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
      <div className="flex-between" style={{ marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: 0 }}>{project.title || "未命名小说"}</h2>
          <span className="text-muted">{project.genre}</span>
        </div>
        {project.status === "ready" && (
          <button className="btn" onClick={() => navigate(`/projects/${project.id}/chapters`)}>
            前往章节 →
          </button>
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
        <div className="card mt-24">
          {result.consistency_warnings.length > 0 && (
            <div className="banner banner-warn">
              ⚠ 一致性校验发现 {result.consistency_warnings.length} 条待复核（重名/孤儿节点/悬空引用），
              详见"一致性校验"标签页，不影响其他设定的使用。
            </div>
          )}

          <div className="tabs">
            {TABS.map((t) => (
              <button
                key={t}
                className={`tab-btn ${activeTab === t ? "active" : ""}`}
                onClick={() => setActiveTab(t)}
              >
                {t}
                {t === "一致性校验" && result.consistency_warnings.length > 0 && (
                  <> ({result.consistency_warnings.length})</>
                )}
              </button>
            ))}
          </div>

          {activeTab === "世界观" && result.world_setting && (
            <div>
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
                  <div className="entity-card__name">{v.title}</div>
                  <div className="entity-card__desc">{v.summary}</div>
                </div>
              ))}
            </div>
          )}

          {activeTab === "人物" &&
            result.characters.map((c) => (
              <div className="entity-card" key={c.id}>
                <span className="entity-card__name">{c.name}</span>
                <span className="entity-card__meta">{c.role}</span>
                <div className="entity-card__desc">{String(c.detail_json?.detail ?? "")}</div>
              </div>
            ))}

          {activeTab === "人物关系" &&
            result.relations.map((r) => {
              const fromName = result.characters.find((c) => c.id === r.from_id)?.name || r.from_id;
              const toName = result.characters.find((c) => c.id === r.to_id)?.name || r.to_id;
              return (
                <div className="entity-card" key={r.id}>
                  <span className="entity-card__name">
                    {fromName} → {toName}
                  </span>
                  <span className="entity-card__meta">{r.relation}</span>
                  <div className="entity-card__desc">{r.description}</div>
                </div>
              );
            })}

          {activeTab === "势力" &&
            result.factions.map((f) => (
              <div className="entity-card" key={f.id}>
                <span className="entity-card__name">{f.name}</span>
                <div className="entity-card__desc">{String(f.detail_json?.detail ?? "")}</div>
              </div>
            ))}

          {activeTab === "地图" &&
            result.map_nodes.map((m) => (
              <div className="entity-card" key={m.id}>
                <span className="entity-card__name">{m.name}</span>
                <span className="entity-card__meta">{m.level}</span>
                <div className="entity-card__desc">{m.description}</div>
              </div>
            ))}

          {activeTab === "力量体系" &&
            result.power_systems.map((p) => (
              <div className="entity-card" key={p.id}>
                <div className="entity-card__name">{p.name}</div>
                <div className="entity-card__desc">{p.description}</div>
                <div className="mt-24">
                  {p.tiers_json?.map((t) => (
                    <div key={t.level} className="text-muted" style={{ fontSize: "0.85rem" }}>
                      {t.level}. {t.name}
                    </div>
                  ))}
                </div>
              </div>
            ))}

          {activeTab === "货币" &&
            result.currencies.map((c) => (
              <div className="entity-card" key={c.id}>
                <span className="entity-card__name">{c.name}</span>
                <div className="entity-card__desc">{String(c.detail_json?.detail ?? "")}</div>
              </div>
            ))}

          {activeTab === "伏笔" &&
            result.foreshadowings.map((f) => (
              <div className="entity-card" key={f.id}>
                <span className="badge badge-draft">{f.importance}</span>{" "}
                <span className="entity-card__meta">{f.status}</span>
                <div className="entity-card__desc">{f.content}</div>
              </div>
            ))}

          {activeTab === "一致性校验" &&
            (result.consistency_warnings.length === 0 ? (
              <div className="empty-state">没有发现明显的结构性问题 ✓</div>
            ) : (
              result.consistency_warnings.map((w, i) => (
                <div className="entity-card" key={i}>
                  <span className="entity-card__name mono">{w.type}</span>
                  <div className="entity-card__desc">{w.detail.join("、")}</div>
                </div>
              ))
            ))}
        </div>
      )}
    </div>
  );
}
