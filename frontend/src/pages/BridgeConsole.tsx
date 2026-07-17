import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  BridgeLogLine, BridgePendingItem, BridgeStatus, BridgeBudget,
  ChapterListItem, Project,
} from "../types";
import { useReveal } from "../hooks/useReveal";
import { useToast } from "../components/Toast";

type PanelData = BridgeStatus | BridgePendingItem[] | Record<string, unknown>[] | Record<string, unknown> | null;

const RUN_COMMANDS = [
  { label: "生成设定包", command: "planner", args: [] },
  { label: "黄金三章", command: "bootstrap", args: [] },
  { label: "写10章", command: "run", args: ["10"] },
  { label: "写10章 (草稿)", command: "run_draft", args: ["10"] },
  { label: "质量看板", command: "dashboard", args: [] },
  { label: "一致性扫描", command: "scan", args: [] },
  { label: "文风指纹", command: "fingerprint", args: [] },
];

// 七要素剧情模型：欲望 / 阻碍 / 行动 / 结果 / 意外 / 转折 / 结局
const PLOT_WHEEL = ["欲望", "阻碍", "行动", "结果", "意外", "转折", "结局"];

// 多模式大纲
const OUTLINE_MODES = [
  { key: "batch", label: "传统批量", desc: "线性序列生成，效率优先" },
  { key: "card", label: "抽卡探索", desc: "多分支概率推理，提供 3-5 种走向" },
  { key: "talk", label: "对话头脑风暴", desc: "实时人机协作推理，深度打磨" },
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
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chapters, setChapters] = useState<ChapterListItem[]>([]);
  const [outlineMode, setOutlineMode] = useState<string>("batch");
  const [plotStep, setPlotStep] = useState<number>(0); // 当前 chapter 在七要素中的进度
  const [budget, setBudget] = useState<BridgeBudget | null>(null);
  const [budgetLoading, setBudgetLoading] = useState(false);
  const [autoscroll, setAutoscroll] = useState(true);  // 日志自动滚动开关
  const eventSourceRef = useRef<EventSource | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const nodeFlipRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();
  useReveal(rootRef);

  useEffect(() => {
    if (!projectId) return;
    api.getProject(projectId).then(setProject).catch((e) => setError(String(e)));
    api.getNovelAIBinding(projectId)
      .then((binding) => {
        setNovelAiDir(binding.novel_ai_dir);
        setNovelId(binding.novel_id);
      })
      .catch((e) => {
        setNovelAiDir("");
        // binding 缺失不算静默 — 让用户知道要先去 binding 页面设置
        // （之前 .catch(()=>{}) 完全吞掉，用户不知道为啥 bridge 跑不动）
        setError(
          `尚未绑定 NovelAI 目录：${String(e)}。请先在「NovelAI 绑定」卡片填写 novel_ai_dir。`
        );
      });
    api.listChapters(projectId)
      .then(setChapters)
      // 之前 .catch(() => setChapters([])) — listChapters 失败时静默显示空列表，
      // 用户看不到已导入的章节以为是 bug。改为 toast.error 提示真实原因。
      .catch((e) => {
        toast.error("加载章节列表失败", String(e));
        setChapters([]);
      });
    return () => eventSourceRef.current?.close();
  }, [projectId]);

  // 根据章节数推算当前章节在七要素中的位置
  useEffect(() => {
    const n = chapters.length;
    if (n === 0) setPlotStep(0);
    else setPlotStep(((n - 1) % PLOT_WHEEL.length) + 1);
  }, [chapters.length]);

  useEffect(() => {
    if (autoscroll) {
      logEndRef.current?.scrollIntoView({ block: "end" });
    }
  }, [logs, autoscroll]);

  // 当章节数变化时（命令完成 / 重新加载），用 Web Animations API 给时间线一个脉冲
  const prevChapterCountRef = useRef(0);
  useEffect(() => {
    if (chapters.length > prevChapterCountRef.current) {
      const tl = document.querySelector(".chapter-timeline");
      if (tl && typeof (tl as HTMLElement).animate === "function") {
        (tl as HTMLElement).animate(
          [
            { boxShadow: "0 0 0 0 rgba(107,138,253,0.55)" },
            { boxShadow: "0 0 0 6px rgba(107,138,253,0)" },
          ],
          { duration: 1200, easing: "cubic-bezier(.2,.7,.2,1)" },
        );
      }
    }
    prevChapterCountRef.current = chapters.length;
  }, [chapters.length]);

  // 当前节点变化时翻 3D 卡
  useEffect(() => {
    const el = nodeFlipRef.current;
    if (!el) return;
    if (activeNode) {
      el.classList.add("is-flipped");
    } else {
      el.classList.remove("is-flipped");
    }
  }, [activeNode]);

  function appendLog(line: string) {
    setLogs((prev) => [...prev, line]);
  }

  function appendLogLine(event: string, body: string, level: "info" | "ok" | "warn" | "err" = "info") {
    setLogs((prev) => [...prev, JSON.stringify({ ts: new Date().toISOString(), event, body, level })]);
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
    setAutoscroll(true);
    appendLogLine("cmd", `$ ${command}${args.length ? ` ${args.join(" ")}` : ""}` + (outlineMode !== "batch" ? ` [mode=${outlineMode}]` : ""), "info");

    try {
      const run = await api.triggerBridge(projectId, command, args, outlineMode);
      const es = new EventSource(api.bridgeStreamUrl(projectId, run.id));
      eventSourceRef.current = es;

      const handleEvent = (eventName: BridgeLogLine["event"], raw: MessageEvent, level: "info" | "ok" | "warn" | "err" = "info") => {
        const payload: BridgeLogLine = JSON.parse(raw.data);
        const text = payload.line || payload.message || formatPayload(payload.data ?? payload);
        appendLogLine(eventName, text, level);
      };

      es.addEventListener("log", (e) => handleEvent("log", e as MessageEvent));
      // start: 命令开始（来自 _run_bridge_async 推的 {"event": "start", "command": ..., "outline_mode": ...}）
      es.addEventListener("start", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        appendLogLine("start", `${payload.command || "?"}${payload.outline_mode ? ` [mode=${payload.outline_mode}]` : ""}`, "info");
      });
      es.addEventListener("auto_pull_setting_start", (e) => handleEvent("auto_pull_setting_start", e as MessageEvent));
      es.addEventListener("auto_pull_setting_done", (e) => handleEvent("auto_pull_setting_done", e as MessageEvent, "ok"));
      es.addEventListener("auto_import_chapters_start", (e) => {
        appendLogLine("auto_import_chapters_start", "正在从 orchestrator 输出目录拉取章节…", "info");
      });
      es.addEventListener("auto_import_chapters_done", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        const count = Array.isArray(payload.imported) ? payload.imported.length : 0;
        appendLogLine("auto_import_chapters_done", `导入 ${count} 章`, "ok");
      });
      // auto_chain_error: 自动链上一步失败时（planner→pull / run→import）触发
      es.addEventListener("auto_chain_error", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        const msg = payload.message || "unknown";
        setError(`自动链错误：${msg}`);
        appendLogLine("auto_chain_error", msg, "err");
        toast.error("自动链错误", msg);
      });
      es.addEventListener("node_start", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        if (payload.node) setActiveNode(payload.node);
        handleEvent("node_start", e as MessageEvent);
      });
      es.addEventListener("node_end", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        if (payload.node && activeNode === payload.node) setActiveNode(null);
        handleEvent("node_end", e as MessageEvent, "ok");
      });
      // complete: 命令执行完毕（来自 _run_bridge_async 推的 {"event": "complete", "status": ...}）
      es.addEventListener("complete", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        appendLogLine("complete", `status=${payload.status || "?"}`, "ok");
        if (payload.status === "done") {
          // 之前 .catch(() => {}) — 章节列表刷新失败时静默吞掉，用户看到
          // 陈旧的章节数以为是 run 没成功。改为 toast.warn 留信号。
          api.listChapters(projectId!).then(setChapters).catch((e) => {
            toast.warn("刷新章节列表失败", String(e));
          });
          toast.success(`${label} 完成`);
        }
      });
      es.addEventListener("done", (e) => {
        const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
        const code = payload.exit_code ?? 0;
        setExitCode(code);
        appendLogLine("done", `exit code: ${code}`, code === 0 ? "ok" : "err");
        es.close();
        setRunning(false);
        setActiveLabel(null);
        setActiveNode(null);
        // 跑完命令后刷新章节数
        api.listChapters(projectId!).then(setChapters).catch((e) => {
          toast.warn("刷新章节列表失败", String(e));
        });
      });
      es.addEventListener("error", (e) => {
        try {
          const payload: BridgeLogLine = JSON.parse((e as MessageEvent).data);
          const msg = payload.message || "unknown";
          setError(`命令失败：${msg}${payload.traceback ? `\n${payload.traceback}` : ""}`);
          appendLogLine("error", msg, "err");
          toast.error("命令失败", msg);
        } catch {
          appendLogLine("error", "SSE 连接中断", "err");
        }
        es.close();
        setRunning(false);
        setActiveLabel(null);
      });
      es.onerror = () => {
        appendLogLine("error", "SSE 连接中断", "err");
        es.close();
        setRunning(false);
        setActiveLabel(null);
      };
    } catch (e) {
      setError(String(e));
      appendLogLine("error", String(e), "err");
      toast.error("启动命令失败", String(e));
      setRunning(false);
      setActiveLabel(null);
    }
  }

  async function runControl(label: string, task: () => Promise<PanelData>) {
    setError(null);
    setPanelTitle(label);
    appendLogLine("cmd", `$ ${label}`, "info");
    try {
      const data = await task();
      setPanelData(data);
      appendLogLine(label, formatPayload(data), "ok");
      if (label === "待审核" && Array.isArray(data)) setPending(data as BridgePendingItem[]);
    } catch (e) {
      setError(String(e));
      appendLogLine("error", String(e), "err");
      toast.error(`${label} 失败`, String(e));
    }
  }

  async function fetchBudget() {
    if (!projectId) return;
    setError(null);
    setBudgetLoading(true);
    appendLogLine("cmd", "$ 预算报告", "info");
    try {
      const data = await api.getBridgeBudget(projectId);
      setBudget(data);
      setPanelTitle("预算报告");
      setPanelData(data as PanelData);
      appendLogLine("预算报告", `已用 $${data.total_cost_usd.toFixed(4)} · ${data.record_count} 条记录`, "ok");
    } catch (e) {
      setError(String(e));
      appendLogLine("error", String(e), "err");
      toast.error("预算拉取失败", String(e));
    } finally {
      setBudgetLoading(false);
    }
  }

  async function submitReview(item: BridgePendingItem, action: "accept" | "reject" | "edit") {
    if (!projectId) return;
    setError(null);
    try {
      await api.submitReview(projectId, {
        task_id: item.task_id,
        action,
        content: action === "edit" ? reviewText[item.task_id] || item.content || "" : undefined,
      });
      appendLogLine("review", `${item.task_id} -> ${action}`, "ok");
      toast.success("审核已提交", `${item.task_id} · ${action}`);
      const fresh = await api.getBridgePending(projectId);
      setPending(fresh);
      setPanelTitle("待审核");
      setPanelData(fresh);
    } catch (e) {
      setError(String(e));
      toast.error("审核提交失败", String(e));
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
      appendLogLine("binding", binding.novel_ai_dir, "ok");
      toast.success("绑定已保存", binding.novel_ai_dir);
    } catch (e) {
      setError(String(e));
      toast.error("保存失败", String(e));
    }
  }

  // 记忆层温度：从章节数据粗略推算
  // L2 热 = 高频上场章节数；L5 弧 = 已写章节数 / 25 (4 弧基准)
  const totalWords = chapters.reduce((a, c) => a + c.word_count, 0);
  const memPips = Math.min(10, Math.max(0, Math.ceil(chapters.length / 1.5))); // 0-10
  const arcPips = Math.min(10, Math.max(0, Math.ceil((chapters.length / 25) * 10))); // 0-10
  const pipsToShow = Array.from({ length: 10 }, (_, i) => i < memPips);

  if (!projectId) return <div className="banner banner-danger">缺少项目 ID。</div>;

  return (
    <div ref={rootRef}>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">
            写作控制台
            {activeLabel && <span className="badge-stamp">{activeLabel}</span>}
          </h1>
          <div className="page-header__sub">
            {project?.title || "未命名小说"} · 调 novel_AI 引擎跑命令
          </div>
        </div>
        <div className="page-header__actions">
          <Link className="btn" to={`/projects/${projectId}/rules`}>
            规则中心
          </Link>
          <Link className="btn" to={`/projects/${projectId}/chapters`}>
            查看章节
          </Link>
        </div>
      </div>

      {error && <div className="banner banner-danger">{error}</div>}
      {exitCode !== null && <div className="banner banner-success">命令完成，exit code: {exitCode}</div>}

      {/* ============ ① 三道记忆防线 + ② 多模式大纲 / 七要素概览（五期：默认折叠） ============ */}
      <details className="card mt-24">
        <summary style={{ cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h3 className="card__title" style={{ margin: 0 }}>实时记忆层 · 当前节点状态</h3>
          <span className="text-faint" style={{ fontSize: 12 }}>▼ 点击展开/折叠</span>
        </summary>

        {/* 3D 翻牌：当前节点状态 */}
        <div className="perspective-3d" style={{ marginBottom: 14, height: 64 }}>
          <div
            ref={nodeFlipRef}
            className="flip-3d"
            style={{ position: "relative", height: "100%" }}
          >
            <div
              className="flip-3d__face banner banner-info"
              style={{ margin: 0, display: "flex", alignItems: "center", gap: 10 }}
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--accent-strong)" strokeWidth="1.5" strokeLinecap="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 2" />
              </svg>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                IDLE · 等待命令
              </span>
              <span className="text-faint" style={{ fontSize: 11, marginLeft: "auto" }}>点击运行命令触发翻牌</span>
            </div>
            <div
              className="flip-3d__face flip-3d__face--back banner"
              style={{
                margin: 0,
                background: "var(--accent-soft)",
                borderColor: "var(--accent)",
                color: "var(--accent-strong)",
                display: "flex",
                alignItems: "center",
                gap: 10,
                boxShadow: "0 0 18px rgba(107,138,253,0.35)",
              }}
            >
              <span
                className="dial-pulse"
                style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--accent-strong)" }}
              />
              <strong style={{ fontFamily: "var(--font-display)", fontSize: 14 }}>{activeNode ?? "—"}</strong>
              <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 11.5 }}>运行中…</span>
            </div>
          </div>
        </div>

        {/* 三个温度计：L2 衔接锁热度 · L3 压缩存储容量 · L5 弧进度 */}
        <div className="memory-stack" style={{ marginBottom: 18 }}>
          <div className="thermo">
            <span className="thermo__label">L2 衔接锁热度</span>
            <div className="thermo__pip-row">
              {pipsToShow.map((filled, i) => (
                <span
                  key={i}
                  className={`thermo__pip ${filled ? (i < 4 ? "is-cold" : i < 7 ? "is-cool" : i < 9 ? "is-warm" : "is-hot") : ""}`}
                  style={filled ? undefined : { opacity: 0.35 }}
                />
              ))}
            </div>
            <span className="thermo__legend">{chapters.length} 章</span>
          </div>
          <div className="thermo">
            <span className="thermo__label">L3 压缩存储</span>
            <div className="thermo__pip-row">
              {Array.from({ length: 10 }, (_, i) => i < Math.min(10, Math.floor(Math.log10(Math.max(1, totalWords)) * 2))).map((_, i) => (
                <span key={i} className="thermo__pip is-cool" />
              ))}
            </div>
            <span className="thermo__legend">{totalWords.toLocaleString()} 字</span>
          </div>
          <div className="thermo">
            <span className="thermo__label">L5 弧进度</span>
            <div className="thermo__pip-row">
              {Array.from({ length: 10 }, (_, i) => i < arcPips).map((_, i) => (
                <span key={i} className="thermo__pip is-cold" style={{ background: "var(--accent)", borderColor: "var(--accent)" }} />
              ))}
            </div>
            <span className="thermo__legend">弧 {Math.floor(chapters.length / 25) + 1}/4</span>
          </div>
        </div>

        {/* 章节时间线 sparkline（最近 30 章字数曲线） */}
        {chapters.length > 0 && (() => {
          const tail = chapters.slice(-30);
          const W = 600, H = 56;
          const max = Math.max(1, ...tail.map((c) => c.word_count));
          const pts = tail.map((c, i) => {
            const x = (i / Math.max(1, tail.length - 1)) * (W - 8) + 4;
            const y = H - 6 - (c.word_count / max) * (H - 12);
            return [x, y] as const;
          });
          const linePath = pts.map((p, i) => (i === 0 ? `M ${p[0]} ${p[1]}` : `L ${p[0]} ${p[1]}`)).join(" ");
          const areaPath = `${linePath} L ${pts[pts.length - 1][0]} ${H} L ${pts[0][0]} ${H} Z`;
          return (
            <div className="chapter-timeline">
              <div className="chapter-timeline__head">
                <span>章节时间线 · 最近 {tail.length} 章字数</span>
                <span>峰值 {max.toLocaleString()} 字</span>
              </div>
              <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
                <defs>
                  <linearGradient id="tl-grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--accent-strong)" stopOpacity="0.65" />
                    <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.0" />
                  </linearGradient>
                </defs>
                <path d={areaPath} className="tl-area" />
                <path d={linePath} className="tl-spark" />
                {pts.map(([x, y], i) => (
                  <circle
                    key={i}
                    cx={x} cy={y}
                    r={i === pts.length - 1 ? 4 : 2}
                    className={i === pts.length - 1 ? "tl-pulse" : "tl-dot"}
                  />
                ))}
              </svg>
            </div>
          );
        })()}

        {/* 七要素剧情模型：wheel */}
        <h3 className="module-heading">
          <span className="module-heading__index">M03</span>
          七要素剧情模型 · 当前章节进度
          <span className="module-heading__sub">
            {chapters.length > 0 ? `正在写第 ${chapters.length + 1} 章` : "尚未开始"}
          </span>
        </h3>
        <div className="plot-wheel">
          {PLOT_WHEEL.map((label, i) => {
            const step = i + 1;
            const state = plotStep === 0 ? "future" : step < plotStep ? "done" : step === plotStep ? "active" : "future";
            return (
              <div key={label} className={`plot-wheel__step is-${state}`}>
                <span className="plot-wheel__pip">{["欲", "阻", "行", "果", "外", "转", "结"][i]}</span>
                <span className="plot-wheel__label">{label}</span>
              </div>
            );
          })}
        </div>

        {/* 多模式大纲 chip */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          <span className="text-muted" style={{ fontSize: 12, marginRight: 4 }}>大纲模式</span>
          {OUTLINE_MODES.map((m) => (
            <button
              key={m.key}
              className={`mode-chip ${outlineMode === m.key ? "is-active" : ""}`}
              onClick={() => setOutlineMode(m.key)}
              title={m.desc}
            >
              {m.label}
            </button>
          ))}
        </div>
      </details>

      {/* ============ novel_AI 目录绑定（保留） ============ */}
      <div className="card mt-24">
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

      {/* ============ 命令区（保留） ============ */}
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
          <button className="btn" disabled={running || budgetLoading} onClick={fetchBudget}>
            {budgetLoading ? "拉取中…" : "预算报告"}
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

      {/* ============ 实时日志 ============ */}
      <div className="card mt-24">
        <div className="flex-between" style={{ marginBottom: 14 }}>
          <h3 className="card__title" style={{ margin: 0 }}>
            实时日志
            {logs.length > 0 && (
              <span className="text-mono text-faint" style={{ fontSize: 11, fontWeight: 400, marginLeft: 8 }}>
                {logs.length} 行
              </span>
            )}
          </h3>
          <div className="button-row">
            <label className="check-row" style={{ minHeight: 0, margin: 0, fontSize: 12, color: "var(--text-muted)", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={autoscroll}
                onChange={(e) => setAutoscroll(e.target.checked)}
                style={{ width: "auto" }}
              />
              自动滚动
            </label>
            <button className="btn btn-sm" onClick={() => setLogs([])}>清空</button>
          </div>
        </div>
        <div className="log-stream" onClick={() => setAutoscroll(false)}>
          {logs.length === 0 ? (
            <div className="text-faint" style={{ fontSize: 12, padding: "8px 0" }}>等待命令…</div>
          ) : (
            logs.map((raw, i) => {
              // 解析 JSON 行（来自 appendLogLine）
              let parsed: { ts?: string; event?: string; body?: string; level?: "info" | "ok" | "warn" | "err" } | null = null;
              try { parsed = JSON.parse(raw); } catch { /* 旧 plain 行兼容 */ }
              if (parsed && parsed.ts) {
                return (
                  <div key={i} className={`log-line log-line--${parsed.level || "info"}`}>
                    <span className="log-line__ts">{(parsed.ts || "").slice(11, 19)}</span>
                    <span className="log-line__body">
                      {parsed.event && <span className="text-faint text-mono" style={{ marginRight: 8 }}>[{parsed.event}]</span>}
                      {parsed.body}
                    </span>
                  </div>
                );
              }
              return (
                <div key={i} className="log-line log-line--info">
                  <span className="log-line__ts">--:--:--</span>
                  <span className="log-line__body">{raw}</span>
                </div>
              );
            })
          )}
          <div ref={logEndRef} />
        </div>
      </div>

      {/* ============ 数据面板（保留） ============ */}
      {panelTitle && !(budget && panelTitle === "预算报告") && (
        <div className="card mt-24">
          <h3 className="card__title">{panelTitle}</h3>
          <pre className="json-panel">{formatPayload(panelData)}</pre>
        </div>
      )}

      {/* ============ BridgeBudget 专用面板 ============ */}
      {budget && (
        <div className="card mt-24">
          <div className="flex-between" style={{ marginBottom: 14 }}>
            <h3 className="card__title" style={{ margin: 0 }}>预算报告</h3>
            <span className="text-faint" style={{ fontSize: 11 }}>共 {budget.record_count} 条 LLM 调用记录</span>
          </div>
          {(() => {
            const used = budget.total_cost_usd;
            const limit = budget.budget_limit_usd ?? 0;
            const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
            const colorClass = pct < 50 ? "budget-ok" : pct < 80 ? "budget-warn" : "budget-crit";
            return (
              <>
                <div className="budget-summary">
                  <span className={`budget-summary__amount budget-summary__amount--${colorClass}`}>
                    ${used.toFixed(4)}
                  </span>
                  <span className="text-faint budget-summary__limit">
                    / ${limit.toFixed(0)} ({pct}%)
                  </span>
                  {pct >= 80 && (
                    <span className={`badge budget-summary__alert budget-summary__alert--${pct >= 95 ? "crit" : "warn"}`}>
                      {pct >= 95 ? "🚨 临界" : "⚠ 接近上限"}
                    </span>
                  )}
                </div>
                <div className="progress-track budget-progress">
                  <div
                    className={`progress-fill budget-progress__fill--${colorClass}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>

                {budget.records.length > 0 && (
                  <div className="budget-records">
                    <h4 className="module-heading" style={{ fontSize: 13, marginBottom: 8 }}>
                      调用明细（按时间倒序 · 最新 20 条）
                    </h4>
                    <div className="budget-records__scroll">
                      <table className="budget-table">
                        <thead>
                          <tr>
                            <th>时间</th>
                            <th>角色</th>
                            <th>模型</th>
                            <th className="num">章节</th>
                            <th className="num">in / out</th>
                            <th className="num">费用</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...budget.records].reverse().slice(0, 20).map((r, i) => (
                            <tr key={i}>
                              <td className="ts">{(r.ts || "").slice(11, 19)}</td>
                              <td>{r.agent}</td>
                              <td className="model">{r.model}</td>
                              <td className="num">{r.chapter || "—"}</td>
                              <td className="num faint">{r.input_tokens} / {r.output_tokens}</td>
                              <td className={`num cost budget-summary__amount--${colorClass}`}>
                                ${r.cost_usd.toFixed(4)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* ============ 待审核（保留） ============ */}
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
