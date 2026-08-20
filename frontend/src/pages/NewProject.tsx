import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { LLMStatusBanner } from "../components/LLMStatusBanner";

const GENRES = [
  { key: "玄幻", icon: "🐉", desc: "异界大陆 · 升级蜕变 · 宗门争霸" },
  { key: "仙侠", icon: "⚔️", desc: "修真寻道 · 飞升渡劫 · 法宝灵宠" },
  { key: "都市", icon: "🏙️", desc: "商战神豪 · 隐世高手 · 逆袭崛起" },
  { key: "科幻", icon: "🚀", desc: "星际探索 · 赛博朋克 · 机械飞升" },
  { key: "历史", icon: "📜", desc: "权谋争霸 · 架空历史 · 盛世风华" },
  { key: "悬疑", icon: "🔍", desc: "诡秘探案 · 惊悚解谜 · 心理博弈" },
  { key: "奇幻", icon: "💫", desc: "魔法王国 · 史诗巨著 · 勇者冒险" },
  { key: "末世", icon: "☣️", desc: "废土生存 · 庇护经营 · 异能爆发" },
  { key: "游戏", icon: "🎮", desc: "虚拟现实 · 数据面板 · 职业竞赛" },
  { key: "武侠", icon: "🗡️", desc: "江湖恩仇 · 绝学传世 · 侠义千秋" },
  { key: "言情", icon: "🌸", desc: "甜宠蜜恋 · 宿命羁绊 · 情绪拉扯" },
  { key: "军事", icon: "🎖️", desc: "铁血军旅 · 现代战争 · 战术指挥" },
];

const TROPES = [
  "系统流", "升级流", "无敌流", "种田流", "重生流",
  "穿越流", "技术流", "经营流", "直播流", "签到流",
  "退婚打脸", "马甲流", "克苏鲁", "灵气复苏", "幕后黑手",
];

const AUDIENCES = ["男频·青年向", "女频·青年向", "男频·成人向", "女频·成人向"];
const LENGTH_RANGES = ["30-80万字（中篇）", "100-200万字（长篇）", "200-400万字（长篇）", "400万字以上（超长篇）"];

export default function NewProject() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState("都市");
  const [audience, setAudience] = useState(AUDIENCES[0]);
  const [tropes, setTropes] = useState<string[]>(["系统流"]);
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
      navigate(`/projects/${project.id}/theme`);
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  }

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", paddingBottom: 60 }}>
      {/* 顶部标题区 */}
      <div style={{ marginBottom: 24 }}>
        <button
          type="button"
          onClick={() => navigate("/")}
          style={{
            background: "transparent",
            border: "none",
            color: "#94A3B8",
            fontSize: 13,
            cursor: "pointer",
            padding: 0,
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            marginBottom: 12,
          }}
        >
          ← 返回作品库
        </button>
        <h1 style={{ fontSize: 26, fontWeight: 700, margin: "0 0 6px", color: "#F8FAFC" }}>
          新建小说创作工坊
        </h1>
        <p style={{ color: "#94A3B8", fontSize: 13.5, margin: 0 }}>
          填写小说基本构想与题材，AI 引擎将自动为您铺设完整的题材画像、世界观架构与黄金开篇。
        </p>
      </div>

      <LLMStatusBanner />

      <div
        style={{
          background: "#131724",
          border: "1px solid rgba(255, 255, 255, 0.08)",
          borderRadius: 16,
          padding: "28px 32px",
          boxShadow: "0 12px 36px rgba(0,0,0,0.35)",
          display: "flex",
          flexDirection: "column",
          gap: 24,
        }}
      >
        {/* 小说名称 */}
        <div>
          <label style={{ fontSize: 13.5, fontWeight: 600, color: "#F1F5F9", display: "block", marginBottom: 8 }}>
            小说书名
            <span style={{ fontSize: 12, color: "#64748B", fontWeight: 400, marginLeft: 8 }}>（留空则由 AI 自动生成霸气书名）</span>
          </label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例如：万界独尊、夜幕降临、从宗门杂役到绝世剑仙…"
            style={{
              width: "100%",
              background: "#0D1019",
              border: "1px solid rgba(255, 255, 255, 0.12)",
              borderRadius: 10,
              padding: "12px 16px",
              fontSize: 14,
              color: "#F8FAFC",
            }}
          />
        </div>

        {/* 题材类型网格选择器 */}
        <div>
          <label style={{ fontSize: 13.5, fontWeight: 600, color: "#F1F5F9", display: "block", marginBottom: 12 }}>
            核心题材类型
          </label>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
              gap: 10,
            }}
          >
            {GENRES.map((g) => {
              const isSelected = genre === g.key;
              return (
                <div
                  key={g.key}
                  onClick={() => setGenre(g.key)}
                  style={{
                    padding: "12px 14px",
                    borderRadius: 10,
                    cursor: "pointer",
                    background: isSelected ? "rgba(99, 102, 241, 0.18)" : "#0D1019",
                    border: `1px solid ${isSelected ? "#6366F1" : "rgba(255, 255, 255, 0.08)"}`,
                    boxShadow: isSelected ? "0 0 16px rgba(99, 102, 241, 0.3)" : "none",
                    transition: "all 0.18s ease",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                >
                  <span style={{ fontSize: 22 }}>{g.icon}</span>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: isSelected ? "#A5B4FC" : "#F1F5F9" }}>
                      {g.key}
                    </div>
                    <div style={{ fontSize: 11, color: "#64748B", marginTop: 2 }}>
                      {g.desc}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 叙事套路标签 */}
        <div>
          <label style={{ fontSize: 13.5, fontWeight: 600, color: "#F1F5F9", display: "block", marginBottom: 10 }}>
            叙事套路与热门元素
            <span style={{ fontSize: 12, color: "#64748B", fontWeight: 400, marginLeft: 8 }}>（可多选）</span>
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {TROPES.map((t) => {
              const isChecked = tropes.includes(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleTrope(t)}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 999,
                    fontSize: 12.5,
                    cursor: "pointer",
                    background: isChecked ? "linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%)" : "#0D1019",
                    border: `1px solid ${isChecked ? "#818CF8" : "rgba(255, 255, 255, 0.10)"}`,
                    color: isChecked ? "#FFFFFF" : "#94A3B8",
                    fontWeight: isChecked ? 600 : 400,
                    boxShadow: isChecked ? "0 2px 10px rgba(99, 102, 241, 0.4)" : "none",
                    transition: "all 0.15s ease",
                  }}
                >
                  {isChecked ? `✓ ${t}` : t}
                </button>
              );
            })}
          </div>
        </div>

        {/* 受众与篇幅 */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
          <div>
            <label style={{ fontSize: 13.5, fontWeight: 600, color: "#F1F5F9", display: "block", marginBottom: 8 }}>
              目标读者受众
            </label>
            <select
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              style={{
                width: "100%",
                background: "#0D1019",
                border: "1px solid rgba(255, 255, 255, 0.12)",
                borderRadius: 10,
                padding: "11px 14px",
                color: "#F8FAFC",
                fontSize: 13.5,
              }}
            >
              {AUDIENCES.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ fontSize: 13.5, fontWeight: 600, color: "#F1F5F9", display: "block", marginBottom: 8 }}>
              规划篇幅字数
            </label>
            <select
              value={lengthRange}
              onChange={(e) => setLengthRange(e.target.value)}
              style={{
                width: "100%",
                background: "#0D1019",
                border: "1px solid rgba(255, 255, 255, 0.12)",
                borderRadius: 10,
                padding: "11px 14px",
                color: "#F8FAFC",
                fontSize: 13.5,
              }}
            >
              {LENGTH_RANGES.map((l) => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </div>
        </div>

        {/* 主要冲突与创意灵感 */}
        <div>
          <label style={{ fontSize: 13.5, fontWeight: 600, color: "#F1F5F9", display: "block", marginBottom: 8 }}>
            核心创意 / 主要矛盾与金手指设想
          </label>
          <textarea
            rows={4}
            value={mainConflict}
            onChange={(e) => setMainConflict(e.target.value)}
            placeholder="简要描述故事的核心看点、金手指设定或主角面对的巨大危机…（例如：主角获得修仙模拟器，每死一次就能继承一项顶级天赋，为了拯救濒临灭绝的宗门开始疯狂作死…）"
            style={{
              width: "100%",
              background: "#0D1019",
              border: "1px solid rgba(255, 255, 255, 0.12)",
              borderRadius: 10,
              padding: "12px 16px",
              fontSize: 13.5,
              color: "#F8FAFC",
              lineHeight: 1.6,
            }}
          />
        </div>

        {error && (
          <div className="banner banner-danger" style={{ margin: 0 }}>
            {error}
          </div>
        )}

        {/* 提交按钮区 */}
        <div style={{ display: "flex", alignItems: "center", gap: 14, paddingTop: 10, borderTop: "1px solid rgba(255, 255, 255, 0.08)" }}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={submitting}
            style={{
              flex: 1,
              padding: "14px 24px",
              fontSize: 15,
              fontWeight: 700,
              borderRadius: 10,
            }}
          >
            {submitting ? "正在初始化小说设定…" : "✨ 立即创建并进入设定工程 →"}
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => navigate("/")}
            style={{ padding: "14px 20px" }}
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
