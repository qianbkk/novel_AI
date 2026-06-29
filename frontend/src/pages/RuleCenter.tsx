import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Project } from "../types";

// 六大模块之 ⑥ ：AI 治理与规则中心
// 后端目前没有 rules/ 的 REST 接口，这里以"工程模板 + 本地草稿"的形式呈现
// 写作风格 / 禁忌词 / 提示词模板 / 后处理工具箱 — 章节执行时由 orchestrator 调度

const STYLE_PRESETS = [
  {
    key: "webnovel",
    label: "网文轻快",
    sample: "节奏明快，爽点密集，对话口语化",
    chips: ["爽点", "金手指", "短句对白"],
  },
  {
    key: "literary",
    label: "文学正剧",
    sample: "克制笔法，意在言外，留白充分",
    chips: ["静观", "白描", "意识流"],
  },
  {
    key: "wuxia",
    label: "武侠古风",
    sample: "半文半白，意境先行，招式诗化",
    chips: ["招式诗化", "江湖气", "四字结构"],
  },
];

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

const POST_TOOLS = [
  { key: "logic", label: "逻辑评估报告", desc: "扫描章节逻辑，对比「经典小说」结构生成报告" },
  { key: "venom", label: "逻辑查漏（毒舌模式）", desc: "AI 以极度严苛视角寻找文中不合理处" },
  { key: "deai", label: "去 AI 痕迹化", desc: "过滤高频 AI 词汇，重构机械化句式" },
];

export default function RuleCenter() {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [style, setStyle] = useState<string>("webnovel");
  const [taboos, setTaboos] = useState<string[]>(["不禁", "然而事实上", "值得注意的是"]);
  const [template, setTemplate] = useState<string>(PROMPT_TEMPLATES[1].name);
  const [toolOutputs, setToolOutputs] = useState<Record<string, string>>({});
  const [running, setRunning] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    api.getProject(projectId).then(setProject).catch(() => {});
  }, [projectId]);

  function addTaboo() {
    const v = window.prompt("新增禁忌词 / 短语：");
    if (!v) return;
    setTaboos((prev) => Array.from(new Set([...prev, v.trim()])));
  }

  function removeTaboo(v: string) {
    setTaboos((prev) => prev.filter((x) => x !== v));
  }

  function runTool(key: string) {
    setRunning(key);
    setToolOutputs((prev) => ({ ...prev, [key]: "" }));
    // 离线模拟：1.2s 后给出占位报告。未来对接 POST /projects/:id/rules/post-process
    window.setTimeout(() => {
      setToolOutputs((prev) => ({
        ...prev,
        [key]: `[${key}] 占位输出 — 当前 API 未暴露规则中心后处理端点。\n对接计划：POST /projects/${projectId}/rules/post-process\n请求体：{ tool: "${key}", style: "${style}", taboos: ${JSON.stringify(taboos)} }`,
      }));
      setRunning(null);
    }, 1200);
  }

  if (!projectId) return <div className="banner banner-danger">缺少项目 ID。</div>;

  return (
    <div>
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
        </div>
      </div>

      <div className="banner banner-info">
        规则中心是叙事工程的指挥中枢。当前条目暂存于本地草稿；未来由后端{" "}
        <span className="text-mono">/projects/:id/rules</span> 提供持久化端点。
      </div>

      {/* 文笔风格 */}
      <div className="card">
        <div className="rule-section">
          <span className="rule-section__num">壹</span>
          <span className="rule-section__title">文笔风格</span>
        </div>
        <div className="rule-grid">
          {STYLE_PRESETS.map((s) => (
            <button
              key={s.key}
              className="legislation-card"
              onClick={() => setStyle(s.key)}
              style={{ cursor: "pointer", textAlign: "left", borderColor: style === s.key ? "var(--accent)" : undefined }}
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
      <div className="card mt-24">
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
              className="legislation-card__chip"
              onClick={() => removeTaboo(t)}
              style={{ cursor: "pointer", borderColor: "var(--stamp-border)", color: "var(--stamp)" }}
              title="点击移除"
            >
              ✕ {t}
            </button>
          ))}
          <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={addTaboo}>+ 添加禁忌词</button>
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
                    onClick={() => setTaboos((prev) => Array.from(new Set([...prev, l])))}
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
      <div className="card mt-24">
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
        </div>

        <div className="template-stub">
          {PROMPT_TEMPLATES.find((t) => t.name === template)?.body}
        </div>

        <div className="module-grid-3" style={{ marginTop: 12 }}>
          {PROMPT_TEMPLATES.map((t) => (
            <div className="entity-card" key={t.name}>
              <span className="entity-card__name mono" style={{ fontSize: 12 }}>{t.name}</span>
              <div className="entity-card__desc" style={{ fontSize: 12, marginTop: 6 }}>{t.body.slice(0, 60)}…</div>
            </div>
          ))}
        </div>
      </div>

      {/* 后处理工具箱 */}
      <div className="card mt-24">
        <div className="rule-section">
          <span className="rule-section__num">肆</span>
          <span className="rule-section__title">质量后处理工具箱</span>
        </div>

        <div className="rule-grid">
          {POST_TOOLS.map((t) => (
            <div key={t.key} className="legislation-card">
              <div className="legislation-card__head">
                <span className="legislation-card__title">{t.label}</span>
                <span className="legislation-card__kicker">{t.key}</span>
              </div>
              <span className="legislation-card__desc">{t.desc}</span>
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
    </div>
  );
}
