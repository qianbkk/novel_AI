import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

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
      navigate(`/projects/${project.id}/worldbuild`);
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 640 }}>
      <h2 className="card__title" style={{ fontSize: "1.2rem" }}>
        新建小说
      </h2>

      <div className="field">
        <label>小说名称（留空则 AI 自动取名）</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="输入小说名称" />
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

      <div className="field">
        <label>受众定位</label>
        <select value={audience} onChange={(e) => setAudience(e.target.value)}>
          {AUDIENCES.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>篇幅字数</label>
        <select value={lengthRange} onChange={(e) => setLengthRange(e.target.value)}>
          {LENGTH_RANGES.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
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

      <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting}>
        {submitting ? "创建中…" : "创建并开始构建世界观"}
      </button>
    </div>
  );
}
