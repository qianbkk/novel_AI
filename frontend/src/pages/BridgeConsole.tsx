import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { BridgeLogLine, BridgePendingItem, BridgeStatus, Project } from "../types";

type PanelData = BridgeStatus | BridgePendingItem[] | Record<string, unknown>[] | Record<string, unknown> | null;

const RUN_COMMANDS = [
  { label: "生成设定包", command: "planner", args: [] },
  { label: "黄金三章", command: "bootstrap", args: [] },
  { label: "写10章", command: "run", args: ["10"] },
  { label: "质量看板", command: "dashboard", args: [] },
  { label: "一致性扫描", command: "scan", args: [] },
  { label: "文风指纹", command: "fingerprint", args: [] },
];

export default function BridgeConsole() {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [activeLabel, setActiveLabel] = useState<string | null>(null);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [panelTitle, setPanelTitle] = useState<string | null>(null);
  const [panelData, setPanelData] = useState<PanelData>(null);
  const [pending, setPending] = useState<BridgePendingItem[]>([]);
  const [novelAiDir, setNovelAiDir] = useState("");
  const [novelId, setNovelId] = useState("");
  const [reviewText, setReviewText] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!projectId) return;
    api.getProject(projectId).then(setProject).catch((e) => setError(String(e)));
    api.getNovelAIBinding(projectId)
      .then((binding) => {
        setNovelAiDir(binding.novel_ai_dir);
        setNovelId(binding.novel_id);
      })
      .catch(() => {
        setNovelAiDir("D:\\AI\\Codex_workspace\\Novel_AI\\novel_AI");
      });
    return () => eventSourceRef.current?.close();
  }, [projectId]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [logs]);

  function appendLog(line: string) {
    setLogs((prev) => [...prev, line]);
  }

  function formatPayload(payload: unknown) {
    if (typeof payload === "string") return payload;
    return JSON.stringify(payload, null, 2);
  }

  async function runBridge(label: string, command: string, args: string[]) {
    if (!projectId) return;
    eventSourceRef.current?.close();
    setRunning(true);
    setActiveLabel(label);
    setExitCode(null);
    setError(null);
    appendLog(`$ ${command}${args.length ? ` ${args.join(" ")}` : ""}`);

    try {
      const run = await api.triggerBridge(projectId, command, args);
      const es = new EventSource(api.bridgeStreamUrl(projectId, run.id));
      eventSourceRef.current = es;

      const handleEvent = (eventName: BridgeLogLine["event"], raw: MessageEvent) => {
        const payload: BridgeLogLine = JSON.parse(raw.data);
        const text = payload.line || payload.message || formatPayload(payload.data ?? payload);
        appendLog(`[${eventName}] ${text}`);
      };

      es.addEventListener("log", (e) => handleEvent("log", e as MessageEvent));
      es.addEventListener("auto_pull_setting", (e) => handleEvent("auto_pull_setting", e as MessageEvent));
      es.addEventListener("auto_import_chapters", (e) => handleEvent("auto_import_chapters", e as MessageEvent));
      es.addEventListener("auto_chain_error", (e) => handleEvent("auto_chain_error", e as MessageEvent));
      es.addEventListener("done", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        const code = payload.exit_code ?? 0;
        setExitCode(code);
        appendLog(`[done] exit code: ${code}`);
        es.close();
        setRunning(false);
        setActiveLabel(null);
      });
      es.addEventListener("error", (e) => {
        try {
          handleEvent("error", e as MessageEvent);
        } catch {
          appendLog("[error] SSE 连接中断");
        }
        es.close();
        setRunning(false);
        setActiveLabel(null);
      });
      es.onerror = () => {
        appendLog("[error] SSE 连接中断");
        es.close();
        setRunning(false);
        setActiveLabel(null);
      };
    } catch (e) {
      setError(String(e));
      appendLog(`[error] ${String(e)}`);
      setRunning(false);
      setActiveLabel(null);
    }
  }

  async function runControl(label: string, task: () => Promise<PanelData>) {
    setError(null);
    setPanelTitle(label);
    appendLog(`$ ${label}`);
    try {
      const data = await task();
      setPanelData(data);
      appendLog(`[${label}] ${formatPayload(data)}`);
      if (label === "待审核" && Array.isArray(data)) setPending(data as BridgePendingItem[]);
    } catch (e) {
      setError(String(e));
      appendLog(`[error] ${String(e)}`);
    }
  }

  async function submitReview(item: BridgePendingItem, action: "accept" | "reject" | "edit") {
    if (!projectId) return;
    setError(null);
    try {
      await api.submitReview(projectId, {
        task_id: item.task_id,
        action,
        edited_content: action === "edit" ? reviewText[item.task_id] || item.content || "" : undefined,
      });
      appendLog(`[review] ${item.task_id} -> ${action}`);
      const fresh = await api.getBridgePending(projectId);
      setPending(fresh);
      setPanelTitle("待审核");
      setPanelData(fresh);
    } catch (e) {
      setError(String(e));
    }
  }

  async function saveBinding() {
    if (!projectId || !novelAiDir.trim()) return;
    setError(null);
    try {
      const binding = await api.updateNovelAIBinding(projectId, {
        novel_ai_dir: novelAiDir.trim(),
        novel_id: novelId.trim() || undefined,
      });
      setNovelAiDir(binding.novel_ai_dir);
      setNovelId(binding.novel_id);
      appendLog(`[binding] ${binding.novel_ai_dir}`);
    } catch (e) {
      setError(String(e));
    }
  }

  if (!projectId) return <div className="banner banner-danger">缺少项目 ID。</div>;

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: 0 }}>Bridge Console</h2>
          <span className="text-muted">{project?.title || "未命名小说"}</span>
        </div>
        <Link className="btn" to={`/projects/${projectId}/chapters`}>
          查看章节
        </Link>
      </div>

      {error && <div className="banner banner-danger">{error}</div>}
      {exitCode !== null && <div className="banner banner-success">命令完成，exit code: {exitCode}</div>}

      <div className="card">
        <h3 className="card__title">novel_AI 目录绑定</h3>
        <div className="form-grid">
          <div className="field">
            <label>novel_AI 目录</label>
            <input value={novelAiDir} onChange={(e) => setNovelAiDir(e.target.value)} />
          </div>
          <div className="field">
            <label>Novel ID</label>
            <input value={novelId} onChange={(e) => setNovelId(e.target.value)} placeholder="留空使用项目 ID" />
          </div>
        </div>
        <div className="button-row">
          <button className="btn btn-primary" onClick={saveBinding} disabled={!novelAiDir.trim()}>
            保存绑定
          </button>
        </div>
      </div>

      <div className="card mt-24">
        <h3 className="card__title">命令</h3>
        <div className="command-grid">
          <button
            className="btn btn-primary"
            disabled={running}
            onClick={() => runControl("推送设定", () => api.pushConcept(projectId))}
          >
            推送设定
          </button>
          {RUN_COMMANDS.map((item) => (
            <button
              key={item.label}
              className="btn"
              disabled={running}
              onClick={() => runBridge(item.label, item.command, item.args)}
            >
              {running && activeLabel === item.label ? "运行中…" : item.label}
            </button>
          ))}
          <button className="btn" disabled={running} onClick={() => runControl("查看状态", () => api.getBridgeStatus(projectId))}>
            查看状态
          </button>
          <button className="btn" disabled={running} onClick={() => runControl("预算报告", () => api.getBridgeBudget(projectId))}>
            预算报告
          </button>
          <button className="btn" disabled={running} onClick={() => runControl("待审核", () => api.getBridgePending(projectId))}>
            待审核
          </button>
          <button className="btn" disabled={running} onClick={() => runControl("拉取设定", () => api.pullSetting(projectId))}>
            拉取设定
          </button>
          <button className="btn" disabled={running} onClick={() => runControl("导入章节", () => api.importChapters(projectId))}>
            导入章节
          </button>
        </div>
      </div>

      <div className="card mt-24">
        <div className="flex-between" style={{ marginBottom: 14 }}>
          <h3 className="card__title" style={{ margin: 0 }}>
            实时日志
          </h3>
          <button className="btn" onClick={() => setLogs([])}>
            清空
          </button>
        </div>
        <pre className="log-console">{logs.length ? logs.join("\n") : "等待命令…"}</pre>
        <div ref={logEndRef} />
      </div>

      {panelTitle && (
        <div className="card mt-24">
          <h3 className="card__title">{panelTitle}</h3>
          <pre className="json-panel">{formatPayload(panelData)}</pre>
        </div>
      )}

      {pending.length > 0 && (
        <div className="card mt-24">
          <h3 className="card__title">人工审核</h3>
          {pending.map((item) => (
            <div className="entity-card" key={item.task_id}>
              <span className="entity-card__name">{item.title || item.task_id}</span>
              <span className="entity-card__meta">{item.type || item.status || "pending"}</span>
              <div className="entity-card__desc">{item.content || formatPayload(item)}</div>
              <textarea
                rows={3}
                value={reviewText[item.task_id] ?? item.content ?? ""}
                onChange={(e) => setReviewText((prev) => ({ ...prev, [item.task_id]: e.target.value }))}
                style={{ marginTop: 12 }}
              />
              <div className="button-row mt-24">
                <button className="btn btn-primary" onClick={() => submitReview(item, "accept")}>
                  接受
                </button>
                <button className="btn" onClick={() => submitReview(item, "edit")}>
                  提交编辑
                </button>
                <button className="btn btn-danger" onClick={() => submitReview(item, "reject")}>
                  拒绝
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
