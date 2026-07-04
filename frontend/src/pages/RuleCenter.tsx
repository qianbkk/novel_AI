import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Project, PostProcessResult, RuleConfig } from "../types";
import { useReveal } from "../hooks/useReveal";
import { useToast } from "../components/Toast";

const STYLE_PRESETS = [
  { key: "webnovel", label: "网文轻快", sample: "节奏明快，爽点密集，对话口语化", chips: ["爽点", "金手指", "短句对白"] },
  { key: "literary", label: "文学正剧", sample: "克制笔法，意在言外，留白充分", chips: ["静观", "白描", "意识流"] },
  { key: "wuxia", label: "武侠古风", sample: "半文半白，意境先行，招式诗化", chips: ["招式诗化", "江湖气", "四字结构"] },
] as const;

const TABOO_LIBRARY = [
  { tag: "AI 高频词", list: ["不禁", "然而", "然而事实上", "值得注意的是", "总而言之", "综上所述"] },
  { tag: "逻辑硬伤", list: ["无因果转折", "硬切换镜头", "时间错位"] },
  { tag: "平台禁忌", list: ["涉政", "具体器官描写", "未成人明示"] },
];

const PROMPT_TEMPLATES = [
  { name: "planner.设定生成", body: "你是小说设定编剧。基于以下世界观与人物档案，输出 {n} 个事件单元…" },
  { name: "run.章节撰写", body: "你是本章执笔者。严格按照七要素剧情模型（欲望/阻碍/行动/结果/意外/转折/结局）展开…" },
  { name: "review.逻辑毒舌", body: "你扮演严苛文学编辑。逐段找出本章违背世界立法、人物状态、伏笔回收的硬伤…" },
  { name: "fingerprint.文风指纹", body: "采样最近 10 章，输出用词偏好、句长偏好、转折偏好三元组…" },
];

const POST_TOOLS: Array<{
  key: "logic" | "venom" | "deai";
  label: string;
  desc: string;
  agent_hint: string;
}> = [
  { key: "logic", label: "逻辑评估报告", desc: "扫描章节逻辑，对比「世界立法」生成报告", agent_hint: "checker_main" },
  { key: "venom", label: "逻辑查漏（毒舌模式）", desc: "AI 以极度严苛视角寻找文中不合理处", agent_hint: "checker_cross1" },
  { key: "deai", label: "去 AI 痕迹化", desc: "扫描 AI 高频词，给出替换方案", agent_hint: "rewriter" },
];

// 离线兜底：localStorage 仍可用作「先在 UI 编辑、暂未保存到后端」的临时态
const STORAGE_KEY = (id: string) => `firstdraft:rules:${id}`;

type Saved = { style: string; taboos: string[]; template: string };
function loadSaved(projectId: string): Saved | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY(projectId));
    if (!raw) return null;
    return JSON.parse(raw) as Saved;
  } catch {
    return null;
  }
}
function saveSaved(projectId: string, data: Saved) {
  try {
    localStorage.setItem(STORAGE_KEY(projectId), JSON.stringify(data));
  } catch {
    // ignore
  }
}

// 极简 Web Audio 落笔声（无需加载音频文件）
function playTick(audioCtxRef: React.MutableRefObject<AudioContext | null>, freq = 320, dur = 0.08) {
  try {
    if (!audioCtxRef.current) {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      audioCtxRef.current = new Ctx();
    }
    const ctx = audioCtxRef.current;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(freq * 0.6, ctx.currentTime + dur);
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.005);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + dur + 0.01);
  } catch {
    // 静默失败：用户可能未授权自动播放
  }
}

export default function RuleCenter() {
  const { projectId } = useParams<{ projectId: string }>();
  const toast = useToast();
  const [project, setProject] = useState<Project | null>(null);
  const [style, setStyle] = useState<"webnovel" | "literary" | "wuxia">("webnovel");
  const [taboos, setTaboos] = useState<string[]>([]);
  const [template, setTemplate] = useState<string>(PROMPT_TEMPLATES[1].name);
  const [toolOutputs, setToolOutputs] = useState<Record<string, string>>({});
  const [running, setRunning] = useState<string | null>(null);
  const [flashKey, setFlashKey] = useState<string | null>(null);
  const [lastResults, setLastResults] = useState<Record<string, PostProcessResult>>({});
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [remoteLoaded, setRemoteLoaded] = useState(false);

  const rootRef = useRef<HTMLDivElement | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const dialogInputRef = useRef<HTMLInputElement | null>(null);

  useReveal(rootRef);

  // ── 初始加载：先读远端，再回退到 localStorage，再回退到默认 ──
  useEffect(() => {
    if (!projectId) return;
    api.getProject(projectId).then(setProject).catch((e) => {
      // 之前 .catch(() => {}) — project_id 不存在 / 权限问题时 RuleCenter
      // 显示空白页无任何线索。改为 toast.error 留信号。
      toast.error("加载项目失败", String(e));
    });
    api.getRules(projectId)
      .then((cfg: RuleConfig) => {
        setStyle(cfg.style);
        setTaboos(cfg.taboos || []);
        setTemplate(cfg.template || PROMPT_TEMPLATES[1].name);
        setRemoteLoaded(true);
        setSavedAt(cfg.updated_at ? new Date(cfg.updated_at).getTime() : Date.now());
      })
      .catch(() => {
        // 后端不可用 → 走 localStorage
        const saved = loadSaved(projectId);
        if (saved) {
          setStyle(saved.style as "webnovel" | "literary" | "wuxia");
          setTaboos(saved.taboos);
          setTemplate(saved.template);
          setSavedAt(Date.now());
        }
      });
  }, [projectId]);

  // ── 状态变更 → 防抖写后端 + 同步 localStorage ──
  useEffect(() => {
    if (!projectId || !remoteLoaded) return;
    const t = window.setTimeout(() => {
      saveSaved(projectId, { style, taboos, template });
      api.putRules(projectId, { style, taboos, template })
        .then(() => setSavedAt(Date.now()))
        .catch((e) => setError(String(e)));
    }, 400);
    return () => window.clearTimeout(t);
  }, [projectId, style, taboos, template, remoteLoaded]);

  function openDialog() {
    const d = dialogRef.current;
    if (!d) return;
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
    setTimeout(() => dialogInputRef.current?.focus(), 50);
  }

  function closeDialog() {
    const d = dialogRef.current;
    if (!d) return;
    if (typeof d.close === "function") d.close();
    else d.removeAttribute("open");
  }

  function commitDialog() {
    const v = dialogInputRef.current?.value.trim();
    if (v) {
      setTaboos((prev) => Array.from(new Set([...prev, v])));
      playTick(audioCtxRef, 220, 0.07);
    }
    closeDialog();
  }

  function removeTaboo(v: string) {
    setTaboos((prev) => prev.filter((x) => x !== v));
    playTick(audioCtxRef, 180, 0.10);
  }

  // ── 真实后处理：POST /projects/{id}/rules/post-process ──
  async function runTool(key: "logic" | "venom" | "deai") {
    if (!projectId) return;
    setRunning(key);
    setError(null);
    playTick(audioCtxRef, 220, 0.05);
    try {
      const res = await api.postProcess(projectId, {
        tool: key,
        style,
        taboos,
        // 不传 chapter_no → 后端取最新一章
      });
      setLastResults((prev) => ({ ...prev, [key]: res }));
      const out = [
        `摘要：${res.summary}` + (res.score ? `  评分：${res.score}/10` : ""),
        `成本：$${res.cost_usd.toFixed(4)}  章节：第${res.chapter_no}章`,
        "",
        ...res.findings.map((f) => "  " + (f.line || JSON.stringify(f))),
      ].join("\n");
      setToolOutputs((prev) => ({ ...prev, [key]: out }));
      setFlashKey(key);
      playTick(audioCtxRef, 520, 0.12);
      window.setTimeout(() => setFlashKey(null), 1500);
    } catch (e) {
      setError(String(e));
      setToolOutputs((prev) => ({
        ...prev,
        [key]: `[${key}] 后处理失败：${e}`,
      }));
    } finally {
      setRunning(null);
    }
  }

  // 模板 smoke test（前端即可，无需后端）
  function smokeTestTemplate() {
    const tpl = PROMPT_TEMPLATES.find((t) => t.name === template);
    if (!tpl) return;
    playTick(audioCtxRef, 700, 0.04);
    setToolOutputs((prev) => ({
      ...prev,
      __smoke:
        `Smoke test: ${tpl.name}\n` +
        `Render → ${tpl.body.replace("{n}", "5")}\n` +
        `Style: ${style}\n` +
        `Taboos (${taboos.length}): ${taboos.join("、") || "—"}`,
    }));
  }

  if (!projectId) return <div className="banner banner-danger">缺少项目 ID。</div>;

  return (
    <div ref={rootRef}>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">
            规则中心
            <span className="badge-soft badge" style={{ marginLeft: 8 }}>{project?.title || "未命名小说"}</span>
          </h1>
          <div className="page-header__sub">
            章节执笔时的硬约束：文笔风格 · 禁忌词 · 提示词模板 · 后处理工具箱
          </div>
        </div>
        <div className="page-header__actions">
          <span className="badge-stamp">M06</span>
          {savedAt && (
            <span
              className="text-faint"
              style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}
              title={remoteLoaded ? "已同步到后端 + localStorage" : "仅本地兜底（后端不可达）"}
            >
              {remoteLoaded ? "✓ 已同步到后端" : "⚠ 离线兜底"}
            </span>
          )}
        </div>
      </div>

      {error && <div className="banner banner-danger">{error}</div>}

      <div className="banner banner-info">
        规则中心是叙事工程的指挥中枢。当前生效配置：
        <span className="text-mono"> style={style} · template={template} · taboos={taboos.length} 项</span>
      </div>

      {/* 文笔风格 */}
      <div className="card reveal">
        <div className="rule-section">
          <span className="rule-section__num">壹</span>
          <span className="rule-section__title">文笔风格</span>
        </div>
        <div className="rule-grid">
          {STYLE_PRESETS.map((s) => (
            <button
              key={s.key}
              className="legislation-card"
              onClick={() => {
                setStyle(s.key);
                playTick(audioCtxRef, 360, 0.06);
              }}
              style={{
                cursor: "pointer",
                textAlign: "left",
                borderColor: style === s.key ? "var(--accent)" : undefined,
                background: style === s.key ? "var(--accent-soft)" : undefined,
              }}
            >
              <div className="legislation-card__head">
                <span className="legislation-card__title">{s.label}</span>
                {style === s.key && <span className="badge-stamp" style={{ fontSize: 10 }}>已选</span>}
              </div>
              <span className="legislation-card__desc">{s.sample}</span>
              <div className="legislation-card__chips">
                {s.chips.map((c) => (
                  <span className="legislation-card__chip" key={c}>{c}</span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* 禁忌词 */}
      <div className="card mt-24 reveal">
        <div className="rule-section">
          <span className="rule-section__num">贰</span>
          <span className="rule-section__title">禁忌词过滤</span>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
          {taboos.length === 0 && (
            <span className="text-faint" style={{ fontSize: 12 }}>暂未添加</span>
          )}
          {taboos.map((t) => (
            <button
              key={t}
              className="legislation-card__chip ink-drop"
              onClick={() => removeTaboo(t)}
              style={{ cursor: "pointer", borderColor: "var(--stamp-border)", color: "var(--stamp)" }}
              title="点击移除"
            >
              ✕ {t}
            </button>
          ))}
          <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={openDialog}>
            + 添加禁忌词
          </button>
        </div>

        <div className="module-grid-3">
          {TABOO_LIBRARY.map((g) => (
            <div key={g.tag}>
              <div className="text-muted" style={{ fontSize: 11.5, marginBottom: 6, letterSpacing: "0.05em" }}>{g.tag}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {g.list.map((l) => (
                  <button
                    key={l}
                    className="legislation-card__chip"
                    onClick={() => {
                      setTaboos((prev) => Array.from(new Set([...prev, l])));
                      playTick(audioCtxRef, 460, 0.05);
                    }}
                    style={{ cursor: "pointer" }}
                  >
                    + {l}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 提示词模板 */}
      <div className="card mt-24 reveal">
        <div className="rule-section">
          <span className="rule-section__num">叁</span>
          <span className="rule-section__title">提示词模板</span>
        </div>

        <div className="rule-section">
          <span className="text-muted" style={{ fontSize: 12, marginLeft: 12 }}>已激活：</span>
          <select value={template} onChange={(e) => setTemplate(e.target.value)} style={{ maxWidth: 320 }}>
            {PROMPT_TEMPLATES.map((t) => (
              <option key={t.name} value={t.name}>{t.name}</option>
            ))}
          </select>
          <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={smokeTestTemplate}>
            ▶ 渲染测试
          </button>
        </div>

        <div className="template-stub">
          {PROMPT_TEMPLATES.find((t) => t.name === template)?.body}
        </div>

        {toolOutputs.__smoke && (
          <pre className="log-console" style={{ marginTop: 8, maxHeight: 140, fontSize: 11.5 }}>
            {toolOutputs.__smoke}
          </pre>
        )}

        <div className="module-grid-3" style={{ marginTop: 12 }}>
          {PROMPT_TEMPLATES.map((t) => (
            <div className="entity-card" key={t.name}>
              <span className="entity-card__name mono" style={{ fontSize: 12 }}>{t.name}</span>
              <div className="entity-card__desc" style={{ fontSize: 12, marginTop: 6 }}>{t.body.slice(0, 60)}…</div>
            </div>
          ))}
        </div>
      </div>

      {/* 后处理工具箱 — 真接后端 */}
      <div className="card mt-24 reveal">
        <div className="rule-section">
          <span className="rule-section__num">肆</span>
          <span className="rule-section__title">质量后处理工具箱</span>
          <span className="text-faint" style={{ marginLeft: 8, fontSize: 11 }}>→ POST /projects/&#123;id&#125;/rules/post-process</span>
        </div>

        <div className="rule-grid">
          {POST_TOOLS.map((t) => (
            <div
              key={t.key}
              className={`legislation-card ${flashKey === t.key ? "flash-cell" : ""}`}
            >
              <div className="legislation-card__head">
                <span className="legislation-card__title">{t.label}</span>
                <span className="legislation-card__kicker">{t.agent_hint}</span>
              </div>
              <span className="legislation-card__desc">{t.desc}</span>
              {lastResults[t.key] && (
                <div className="text-faint" style={{ fontSize: 11, marginTop: 4 }}>
                  上次：第{lastResults[t.key].chapter_no}章 · ${lastResults[t.key].cost_usd.toFixed(4)}
                </div>
              )}
              <div className="button-row" style={{ marginTop: 4 }}>
                <button
                  className="btn btn-primary"
                  style={{ fontSize: 12, padding: "5px 12px" }}
                  onClick={() => runTool(t.key)}
                  disabled={running === t.key}
                >
                  {running === t.key ? "运行中…" : "运行"}
                </button>
              </div>
              {toolOutputs[t.key] && (
                <pre className="log-console" style={{ marginTop: 8, maxHeight: 180, fontSize: 11.5 }}>{toolOutputs[t.key]}</pre>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* HTML5 原生 dialog：添加禁忌词 */}
      <dialog
        ref={dialogRef}
        className="ink-dialog"
        onCancel={(e) => {
          e.preventDefault();
          closeDialog();
        }}
        onClose={closeDialog}
      >
        <h3 className="ink-dialog__title">添加禁忌词 / 短语</h3>
        <p className="ink-dialog__sub">加入后，章节生成会自动绕开这个词。</p>
        <input
          ref={dialogInputRef}
          placeholder="如：不禁、值得注意的是…"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commitDialog();
            }
          }}
        />
        <div className="ink-dialog__actions">
          <button className="btn btn-ghost" onClick={closeDialog}>取消</button>
          <button className="btn btn-primary" onClick={commitDialog}>确定</button>
        </div>
      </dialog>
    </div>
  );
}