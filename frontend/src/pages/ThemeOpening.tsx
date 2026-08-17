/** ThemeOpening.tsx - v1.0 Stage I: 主题与开篇编辑页

设计动机（docs/drafts/v1-quality-first-design.md § Stage I）：
- 用户决策：UI 直接编辑 JSON（v1.0 决策）
- 一个页面管 4 个 v1.0 Pre-Production 产物：
  1. Genre Profile（题材画像，模板选定）
  2. Theme Spine（共性主题 + 期待感弧）
  3. Opening Design（黄金三章结构）
  4. Research Notes（资料助手）
- 每个产物：Generate 按钮（LLM 模板生成）+ Edit JSON + Save

CLAUDE.md 红线：用户编辑 PUT 时强制 source='user'，不会被覆盖。
*/

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  GenreProfile, ThemeSpine, ThemeSpineIn,
  OpeningDesign, OpeningDesignIn,
  ResearchNotes, ResearchNotesIn,
} from "../types";

type Tab = "genre" | "theme" | "opening" | "research";

const GENRE_KEYS = [
  { key: "xuanhuan", label: "玄幻" },
  { key: "xianxia", label: "仙侠" },
  { key: "dushi", label: "都市" },
  { key: "lishi", label: "历史" },
  { key: "junshi", label: "军事" },
  { key: "kehuan", label: "科幻" },
] as const;

export default function ThemeOpening() {
  const { projectId } = useParams();
  const [tab, setTab] = useState<Tab>("theme");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  return (
    <div className="page" style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
      <h2 style={{ marginBottom: 8 }}>主题与开篇</h2>
      <p style={{ color: "var(--text-secondary)", marginBottom: 24, fontSize: 14 }}>
        v1.0 前期工程：4 个结构化产物。前期做足 → 写作阶段省心。
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, borderBottom: "1px solid var(--border)" }}>
        {(["genre", "theme", "opening", "research"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => { setTab(t); setError(null); setSuccess(null); }}
            style={{
              padding: "8px 16px",
              border: "none",
              borderBottom: tab === t ? "2px solid var(--accent)" : "2px solid transparent",
              background: "transparent",
              color: tab === t ? "var(--accent)" : "var(--text-secondary)",
              cursor: "pointer",
              fontWeight: tab === t ? 600 : 400,
            }}
          >
            {t === "genre" && "① 题材画像"}
            {t === "theme" && "② 共性主题"}
            {t === "opening" && "③ 黄金三章"}
            {t === "research" && "④ 资料助手"}
          </button>
        ))}
      </div>

      {error && <div style={{ padding: 12, background: "var(--error-bg)", color: "var(--error)", borderRadius: 6, marginBottom: 16 }}>{error}</div>}
      {success && <div style={{ padding: 12, background: "var(--success-bg)", color: "var(--success)", borderRadius: 6, marginBottom: 16 }}>{success}</div>}

      {tab === "genre" && <GenreTab projectId={projectId!} onError={setError} onSuccess={setSuccess} />}
      {tab === "theme" && <ThemeTab projectId={projectId!} onError={setError} onSuccess={setSuccess} />}
      {tab === "opening" && <OpeningTab projectId={projectId!} onError={setError} onSuccess={setSuccess} />}
      {tab === "research" && <ResearchTab projectId={projectId!} onError={setError} onSuccess={setSuccess} />}
    </div>
  );
}

// ════════════════════════════════════════════════════
// ① 题材画像
// ════════════════════════════════════════════════════

function GenreTab({ projectId, onError, onSuccess }: { projectId: string; onError: (e: string) => void; onSuccess: (s: string) => void }) {
  const [profile, setProfile] = useState<GenreProfile | null>(null);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [genreKey, setGenreKey] = useState<string>("xuanhuan");
  const [useLlm, setUseLlm] = useState(false);

  useEffect(() => {
    api.getGenreProfile(projectId).then(setProfile).catch(() => setProfile(null));
  }, [projectId]);

  const handleGenerate = async () => {
    try {
      const result = await api.generateGenreProfile(projectId, { genre_key: genreKey, use_llm: useLlm });
      setProfile(result);
      onSuccess("题材画像已生成");
    } catch (e: unknown) {
      onError(String(e));
    }
  };

  const handleSave = async () => {
    // PUT 通过 re-generate 实现（API 设计：generate 走 LLM 改写模板；
    // 用户编辑后的 profile 暂时没法直接 PUT — 下次补 endpoint；
    // 当前实现：编辑模式下重新 generate to user-edited version）
    onError("编辑保存：当前实现只支持 Generate。请用 LLM 改写或模板生成。");
  };

  if (profile && !editing) {
    return (
      <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
          <strong>{profile.genre} ({profile.genre_key})</strong>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>source: {profile.source ?? "unknown"}</span>
        </div>
        <Field label="核心读者">{profile.reader_persona.primary}</Field>
        <Field label="读者幻想">{profile.reader_persona.core_fantasy}</Field>
        <Field label="调调">{profile.tone_preference}</Field>
        <Field label="禁忌">{profile.taboo.join(" / ")}</Field>
        <Field label="show-item 参考">{profile.show_item_examples.join(" / ")}</Field>
        <Field label="research_strength">{profile.research_strength}</Field>
        <button onClick={() => setEditing(true)} style={btnStyle}>编辑（当前为只读，重新生成覆盖）</button>
      </div>
    );
  }

  return (
    <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
      <h3 style={{ marginTop: 0 }}>生成题材画像</h3>
      <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
        6 个主流男频题材模板（玄幻/仙侠/都市/历史/军事/科幻），LLM 可在模板基础上细化。
      </p>
      <label>题材：</label>
      <select value={genreKey} onChange={(e) => setGenreKey(e.target.value)} style={selectStyle}>
        {GENRE_KEYS.map((g) => (
          <option key={g.key} value={g.key}>{g.label}</option>
        ))}
      </select>
      <label style={{ marginLeft: 16 }}>
        <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
        使用 LLM 细化
      </label>
      <div style={{ marginTop: 12 }}>
        <button onClick={handleGenerate} style={btnPrimary}>生成</button>
      </div>
      {profile && editing && (
        <div style={{ marginTop: 16 }}>
          <button onClick={handleSave} style={btnStyle}>保存编辑（暂未实现，调用 Generate）</button>
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════
// ② 共性主题
// ════════════════════════════════════════════════════

function ThemeTab({ projectId, onError, onSuccess }: { projectId: string; onError: (e: string) => void; onSuccess: (s: string) => void }) {
  const [theme, setTheme] = useState<ThemeSpine | null>(null);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [concept, setConcept] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getTheme(projectId).then(setTheme).catch(() => setTheme(null));
  }, [projectId]);

  const handleGenerate = async () => {
    try {
      const result = await api.generateTheme(projectId, { concept, use_llm: useLlm });
      setTheme(result);
      onSuccess("共性主题已生成");
    } catch (e: unknown) {
      onError(String(e));
    }
  };

  const handleStartEdit = () => {
    if (!theme) return;
    setEditText(JSON.stringify({
      theme_statement: theme.theme_statement,
      expectation_arc: theme.expectation_arc,
      resonance_anchors: theme.resonance_anchors,
      source: "user",
    }, null, 2));
    setEditing(true);
  };

  const handleSave = async () => {
    let parsed: ThemeSpineIn;
    try {
      parsed = JSON.parse(editText);
    } catch (e: unknown) {
      onError(`JSON 解析失败: ${e}`);
      return;
    }
    setSaving(true);
    try {
      await api.putTheme(projectId, parsed);
      setTheme({ ...parsed, source: "user" });
      setEditing(false);
      onSuccess("主题已保存（source=user）");
    } catch (e: unknown) {
      onError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (theme && !editing) {
    return (
      <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
          <strong>共性主题</strong>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>source: {theme.source ?? "unknown"}</span>
        </div>
        <Field label="theme_statement">{theme.theme_statement}</Field>
        <Field label="expectation_arc">seed ch{theme.expectation_arc.seed_chapter} → twist ch{theme.expectation_arc.twist_chapter} → payoff ch{theme.expectation_arc.payoff_chapter}<br />{theme.expectation_arc.description}</Field>
        <Field label="resonance_anchors">{theme.resonance_anchors.join(" / ")}</Field>
        <button onClick={handleStartEdit} style={btnStyle}>编辑 JSON</button>
      </div>
    );
  }

  if (editing) {
    return (
      <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
        <h3 style={{ marginTop: 0 }}>编辑主题（PUT 强制 source=user）</h3>
        <textarea
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          style={{
            width: "100%",
            minHeight: 280,
            fontFamily: "monospace",
            fontSize: 13,
            padding: 8,
            background: "var(--bg-input)",
            color: "var(--text)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        />
        <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
          <button onClick={handleSave} disabled={saving} style={btnPrimary}>{saving ? "保存中..." : "保存"}</button>
          <button onClick={() => setEditing(false)} style={btnStyle}>取消</button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
      <h3 style={{ marginTop: 0 }}>生成共性主题</h3>
      <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
        先做题材画像，再生成主题（依赖 genre_profile）。
      </p>
      <label>用户初始概念：</label>
      <input
        type="text"
        value={concept}
        onChange={(e) => setConcept(e.target.value)}
        placeholder="例如：服徭役主角在回家前夕被征召"
        style={{ ...inputStyle, width: "100%", marginBottom: 8 }}
      />
      <label>
        <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
        使用 LLM 改写
      </label>
      <div style={{ marginTop: 12 }}>
        <button onClick={handleGenerate} style={btnPrimary}>生成</button>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════
// ③ 黄金三章
// ════════════════════════════════════════════════════

function OpeningTab({ projectId, onError, onSuccess }: { projectId: string; onError: (e: string) => void; onSuccess: (s: string) => void }) {
  const [opening, setOpening] = useState<OpeningDesign | null>(null);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [concept, setConcept] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getOpening(projectId).then(setOpening).catch(() => setOpening(null));
  }, [projectId]);

  const handleGenerate = async () => {
    try {
      const result = await api.generateOpening(projectId, { concept, use_llm: useLlm });
      setOpening(result);
      onSuccess("黄金三章已生成");
    } catch (e: unknown) {
      onError(String(e));
    }
  };

  const handleStartEdit = () => {
    if (!opening) return;
    setEditText(JSON.stringify({
      chapter_1_anchor: opening.chapter_1_anchor,
      chapter_2_question: opening.chapter_2_question,
      chapter_3_escalation: opening.chapter_3_escalation,
      source: "user",
    }, null, 2));
    setEditing(true);
  };

  const handleSave = async () => {
    let parsed: OpeningDesignIn;
    try {
      parsed = JSON.parse(editText);
    } catch (e: unknown) {
      onError(`JSON 解析失败: ${e}`);
      return;
    }
    setSaving(true);
    try {
      await api.putOpening(projectId, parsed);
      setOpening({ ...parsed, source: "user" });
      setEditing(false);
      onSuccess("黄金三章已保存（source=user）");
    } catch (e: unknown) {
      onError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (opening && !editing) {
    return (
      <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
          <strong>黄金三章</strong>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>source: {opening.source ?? "unknown"}</span>
        </div>
        <ChapterView title="ch1_anchor" ch={opening.chapter_1_anchor} />
        <ChapterView title="ch2_question" ch={opening.chapter_2_question} />
        <ChapterView title="ch3_escalation" ch={opening.chapter_3_escalation} />
        <button onClick={handleStartEdit} style={btnStyle}>编辑 JSON</button>
      </div>
    );
  }

  if (editing) {
    return (
      <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
        <h3 style={{ marginTop: 0 }}>编辑黄金三章（hook_type 必须是 7 个合法之一）</h3>
        <textarea
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          style={{
            width: "100%",
            minHeight: 480,
            fontFamily: "monospace",
            fontSize: 13,
            padding: 8,
            background: "var(--bg-input)",
            color: "var(--text)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        />
        <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
          <button onClick={handleSave} disabled={saving} style={btnPrimary}>{saving ? "保存中..." : "保存"}</button>
          <button onClick={() => setEditing(false)} style={btnStyle}>取消</button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
      <h3 style={{ marginTop: 0 }}>生成黄金三章</h3>
      <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
        依赖 genre_profile + theme_spine。
      </p>
      <label>概念：</label>
      <input
        type="text"
        value={concept}
        onChange={(e) => setConcept(e.target.value)}
        placeholder="例如：服徭役期满，归家途中被征召"
        style={{ ...inputStyle, width: "100%", marginBottom: 8 }}
      />
      <label>
        <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
        使用 LLM 改写
      </label>
      <div style={{ marginTop: 12 }}>
        <button onClick={handleGenerate} style={btnPrimary}>生成</button>
      </div>
    </div>
  );
}

function ChapterView({ title, ch }: { title: string; ch: { scene: { where: string; who_present: string[] }; hook_type: string; reader_emotion_to_install?: string; reader_question?: string; show_item_seed?: string; show_item_used?: string; expectation_seed?: string; expectation_shift?: string } }) {
  return (
    <div style={{ border: "1px solid var(--border)", padding: 12, borderRadius: 6, marginBottom: 12 }}>
      <strong>{title}</strong>
      <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
        hook: {ch.hook_type} ｜ where: {ch.scene.where} ｜ who: {ch.scene.who_present.join(", ")}
      </div>
      {ch.reader_emotion_to_install && <div>情绪目标：{ch.reader_emotion_to_install}</div>}
      {ch.reader_question && <div>读者问题：{ch.reader_question}</div>}
      {ch.show_item_seed && <div>show-item 播种：{ch.show_item_seed}</div>}
      {ch.show_item_used && <div>show-item 接力：{ch.show_item_used}</div>}
      {ch.expectation_seed && <div>期望播种：{ch.expectation_seed}</div>}
      {ch.expectation_shift && <div>期望推进：{ch.expectation_shift}</div>}
    </div>
  );
}

// ════════════════════════════════════════════════════
// ④ 资料助手
// ════════════════════════════════════════════════════

function ResearchTab({ projectId, onError, onSuccess }: { projectId: string; onError: (e: string) => void; onSuccess: (s: string) => void }) {
  const [notes, setNotes] = useState<ResearchNotes | null>(null);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [concept, setConcept] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getResearchNotes(projectId).then(setNotes).catch(() => setNotes(null));
  }, [projectId]);

  const handleInitialize = async () => {
    try {
      const result = await api.initializeResearchNotes(projectId, { concept, use_llm: useLlm });
      setNotes(result);
      onSuccess(`资料助手已初始化（${result.research_strength}）`);
    } catch (e: unknown) {
      onError(String(e));
    }
  };

  const handleStartEdit = () => {
    if (!notes) return;
    setEditText(JSON.stringify({
      research_strength: notes.research_strength,
      baseline: notes.baseline,
      per_chapter_notes: notes.per_chapter_notes,
      source: "user",
    }, null, 2));
    setEditing(true);
  };

  const handleSave = async () => {
    let parsed: ResearchNotesIn;
    try {
      parsed = JSON.parse(editText);
    } catch (e: unknown) {
      onError(`JSON 解析失败: ${e}`);
      return;
    }
    setSaving(true);
    try {
      await api.putResearchNotes(projectId, parsed);
      setNotes({ ...parsed, source: "user" });
      setEditing(false);
      onSuccess("资料已保存");
    } catch (e: unknown) {
      onError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (notes && !editing) {
    return (
      <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
          <strong>资料助手（research_strength: {notes.research_strength}）</strong>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>source: {notes.source ?? "unknown"}</span>
        </div>
        <h4 style={{ marginBottom: 8 }}>Baseline（按章 query 时返回）</h4>
        {Object.entries(notes.baseline).map(([k, v]) => (
          <Field key={k} label={k}>{v}</Field>
        ))}
        <h4 style={{ marginTop: 12, marginBottom: 8 }}>个人便笺 / 创作记录</h4>
        {Object.entries(notes.per_chapter_notes).map(([k, v]) => (
          <Field key={k} label={`第 ${k} 章`}>{v}</Field>
        ))}
        <button onClick={handleStartEdit} style={btnStyle}>编辑 JSON</button>
      </div>
    );
  }

  if (editing) {
    return (
      <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
        <h3 style={{ marginTop: 0 }}>编辑资料（research_strength 必须是 strong/medium/weak）</h3>
        <textarea
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          style={{
            width: "100%",
            minHeight: 320,
            fontFamily: "monospace",
            fontSize: 13,
            padding: 8,
            background: "var(--bg-input)",
            color: "var(--text)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        />
        <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
          <button onClick={handleSave} disabled={saving} style={btnPrimary}>{saving ? "保存中..." : "保存"}</button>
          <button onClick={() => setEditing(false)} style={btnStyle}>取消</button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: "var(--bg-card)", padding: 16, borderRadius: 8 }}>
      <h3 style={{ marginTop: 0 }}>初始化资料助手</h3>
      <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
        依赖 genre_profile（按 research_strength 三档分流）。
      </p>
      <label>概念：</label>
      <input
        type="text"
        value={concept}
        onChange={(e) => setConcept(e.target.value)}
        placeholder="（可选）"
        style={{ ...inputStyle, width: "100%", marginBottom: 8 }}
      />
      <label>
        <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
        使用 LLM 细化
      </label>
      <div style={{ marginTop: 12 }}>
        <button onClick={handleInitialize} style={btnPrimary}>初始化</button>
      </div>
    </div>
  );
}

// ── Shared style helpers ─────────────────────────

const btnPrimary = {
  padding: "8px 16px",
  background: "var(--accent)",
  color: "white",
  border: "none",
  borderRadius: 4,
  cursor: "pointer",
  fontWeight: 600,
} as const;

const btnStyle = {
  padding: "8px 16px",
  background: "var(--bg-button)",
  color: "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  cursor: "pointer",
  marginTop: 12,
} as const;

const selectStyle = {
  padding: "6px 12px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  background: "var(--bg-input)",
  color: "var(--text)",
} as const;

const inputStyle = {
  padding: "8px 12px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  background: "var(--bg-input)",
  color: "var(--text)",
} as const;

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 14 }}>{children}</div>
    </div>
  );
}