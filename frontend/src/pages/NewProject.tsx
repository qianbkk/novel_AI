import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { LLMStatusBanner } from "../components/LLMStatusBanner";

const GENRES = ["玄幻", "仙侠", "都市", "科幻", "历史", "言情", "悬疑", "武侠", "奇幻", "末世", "游戏", "军事"];
const TROPES = ["系统流", "升级流", "无敌流", "种田流", "重生流", "穿越流", "技术流", "经营流", "直播流", "签到流"];
const AUDIENCES = ["男频·青年向", "女频·青年向", "男频·成人向", "女频·成人向"];
const LENGTH_RANGES = ["30-80万字（中篇）", "100-200万字（长篇）", "200-400万字（长篇）", "400万字以上（超长篇）"];

export default function NewProject() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState("都市");
  const [audience, setAudience] = useState(AUDIENCES[0]);
  const [tropes, setTropes] = useState<string[]>([]);
  const [lengthRange, setLengthRange] = useState(LENGTH_RANGES[2]);
  const [mainConflict, setMainConflict] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleTrope(t: string) {
    setTropes((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      const project = await api.createProject({
        title: title || undefined,
        genre,
        audience,
        config_json: {
          tropes,
          length_range: lengthRange,
          main_conflict: mainConflict,
          structure_mode: "五幕式",
        },
      });
      // 2026-08-18（架构修复 #7）：创建成功后跳到「下一步」页 ——
      // 题材画像 + 主题（v1.0 Pre-Production），而不是直接跳世界构建。
      // v1.0 设计：先做题材画像 + 主题 + 黄金三章，再做宏观弧结构 + 写章节。
      // 旧逻辑跳 worldbuild → 用户进了页面不知道要做什么 → 报告 #3 反馈。
      navigate(`/projects/${project.id}/theme`);
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">新建小说</h1>
          <div className="page-header__sub">
            填好题材和方向，下一步进入前期工程（题材画像 → 主题 → 黄金三章 → 资料）
          </div>
        </div>
      </div>

      {/* 2026-08-18：LLM 状态 banner（用户报告 #3 架构修复）。
          进入"新建项目"页面就告诉用户 LLM 是否就绪，
          避免创建项目后点了"开始构建"才发现后端没配置。 */}
      <LLMStatusBanner />

      {/* 2026-08-18：小白友好旅程说明（架构修复 #7）。
          用户报告"前端布局不合理，什么都不懂的小白也要知道如何用"。
          5 步写作旅程讲清楚每一步在做什么。 */}
      <div className="card" style={{ maxWidth: 720, marginBottom: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>📖 5 步写作旅程</h3>
        <ol style={{ paddingLeft: 18, margin: 0, fontSize: 12.5, color: "var(--text-muted)", lineHeight: 1.7 }}>
          <li><strong>① 创建项目</strong> — 这一步（填标题 + 题材）</li>
          <li><strong>② 题材画像 + 共性主题</strong> — LLM 帮你定题材调性 + 主旨（下一步）</li>
          <li><strong>③ 世界构建</strong> — 自动生成 7 段世界观、角色、势力、地图（10 阶段流水线）</li>
          <li><strong>④ 大纲</strong> — 拆弧 + 每章任务单（爽点 / 钩子 / 字数）</li>
          <li><strong>⑤ 写章节</strong> — 一章一章写，每章带读者期待、伏笔回收、口癖锚点</li>
        </ol>
        <div style={{ marginTop: 10, fontSize: 11.5, color: "var(--text-faint)" }}>
          提示：每一步完成后回到 Dashboard 项目卡，会显示你走到哪一步。
        </div>
      </div>

      <div className="card" style={{ maxWidth: 720 }}>
        <div className="field">
          <label>小说名称（留空则 AI 自动取名）</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="如：破境、长河日落、半城烟沙…"
          />
        </div>

        <div className="field">
          <label>小说类型</label>
          <div className="tag-group">
            {GENRES.map((g) => (
              <button
                key={g}
                className={`tag-btn ${genre === g ? "active" : ""}`}
                onClick={() => setGenre(g)}
                type="button"
              >
                {g}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <label>叙事套路（可多选）</label>
          <div className="tag-group">
            {TROPES.map((t) => (
              <button
                key={t}
                className={`tag-btn ${tropes.includes(t) ? "active" : ""}`}
                onClick={() => toggleTrope(t)}
                type="button"
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <div className="form-grid">
          <div className="field">
            <label>受众定位</label>
            <select value={audience} onChange={(e) => setAudience(e.target.value)}>
              {AUDIENCES.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>篇幅字数</label>
            <select value={lengthRange} onChange={(e) => setLengthRange(e.target.value)}>
              {LENGTH_RANGES.map((l) => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="field">
          <label>主要冲突 / 创作方向</label>
          <textarea
            rows={3}
            value={mainConflict}
            onChange={(e) => setMainConflict(e.target.value)}
            placeholder="简要描述你想写的故事方向或核心创意…"
          />
        </div>

        {error && <div className="banner banner-danger">{error}</div>}

        <div className="button-row" style={{ marginTop: 8 }}>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? "创建中…" : "创建并开始构建世界观 →"}
          </button>
          <button className="btn btn-ghost" onClick={() => navigate("/")}>
            返回项目列表
          </button>
        </div>
      </div>
    </div>
  );
}
