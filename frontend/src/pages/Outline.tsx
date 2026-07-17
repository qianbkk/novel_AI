import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  OutlineOut, ChapterTask, ArcGeneratePayload,
} from "../types";
import { useToast } from "../components/Toast";

/**
 * Outline 页 — 弧级大纲管理
 *
 * 持久化到 DB + 独立页面查看 / 编辑 / 删除 / LLM 重新生成。
 */
export default function Outline() {
  const { projectId } = useParams<{ projectId: string }>();
  const toast = useToast();
  const [outlines, setOutlines] = useState<OutlineOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [generating, setGenerating] = useState<number | null>(null);
  const [expandedArc, setExpandedArc] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 生成 modal state
  const [showGenModal, setShowGenModal] = useState(false);
  const [genForm, setGenForm] = useState<ArcGeneratePayload>({
    arc_id: 1, arc_name: "", arc_goal: "",
    arc_estimated_chapters: 30, arc_climax_chapter_offset: 15,
  });

  useEffect(() => {
    if (!projectId) return;
    refresh();
  }, [projectId]);

  async function refresh() {
    if (!projectId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const list = await api.listOutlines(projectId);
      if (!mountedRef.current) return;
      setOutlines(list);
    } catch (e) {
      if (!mountedRef.current) return;
      const msg = String(e);
      setLoadError(msg);
      toast.error("大纲加载失败", msg);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  async function handleGenerate() {
    if (!projectId) return;
    if (!genForm.arc_name.trim() || !genForm.arc_goal.trim()) {
      toast.warn("请填写弧名称和弧目标");
      return;
    }
    setGenerating(genForm.arc_id);
    try {
      const newOutline = await api.generateOutline(projectId, genForm);
      toast.success("大纲已生成", `${newOutline.arc_name}: ${newOutline.outline_json?.length ?? 0} 章任务`);
      setShowGenModal(false);
      setGenForm({ arc_id: genForm.arc_id + 1, arc_name: "", arc_goal: "", arc_estimated_chapters: 30, arc_climax_chapter_offset: 15 });
      await refresh();
      setExpandedArc(newOutline.id);
    } catch (e) {
      toast.error("生成失败", String(e));
    } finally {
      setGenerating(null);
    }
  }

  async function handleStatus(outline: OutlineOut, status: string) {
    if (!projectId) return;
    try {
      await api.updateOutline(projectId, outline.id, { status });
      toast.success("状态已更新", `${outline.arc_name} → ${status}`);
      await refresh();
    } catch (e) {
      toast.error("更新失败", String(e));
    }
  }

  async function handleDelete(outline: OutlineOut) {
    if (!projectId) return;
    if (!confirm(`删除弧「${outline.arc_name}」？章节任务单也会一起删（不会影响已写的章节）。`)) return;
    try {
      await api.deleteOutline(projectId, outline.id);
      toast.success("已删除", outline.arc_name);
      await refresh();
    } catch (e) {
      toast.error("删除失败", String(e));
    }
  }

  const totalChapters = outlines.reduce((sum, o) => sum + (o.outline_json?.length || 0), 0);
  const approvedCount = outlines.filter((o) => o.status === "approved").length;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">大纲管理</h1>
          <div className="page-header__sub">
            {outlines.length > 0
              ? `${outlines.length} 个弧 · ${totalChapters} 个章节任务 · ${approvedCount} 个已审批`
              : "还没有大纲 · 点击右上角「生成新大纲」让 LLM 帮你拆弧"}
          </div>
        </div>
        <div className="page-header__actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowGenModal(true)}
            disabled={generating !== null}
            aria-label="生成新大纲"
          >
            ✨ 生成新大纲
          </button>
        </div>
      </div>

      {loading && <div className="loading-text">加载中…</div>}

      {loadError && !loading && (
        <div className="banner banner--error" role="alert">
          <span>加载失败：{loadError}</span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => refresh()}
            disabled={loading}
          >
            重试
          </button>
        </div>
      )}

      {!loading && outlines.length === 0 && (
        <div className="card">
          <div className="empty-state empty-state--with-action">
            <div className="empty-state__icon" style={{ fontSize: 36 }}>📋</div>
            <div className="empty-state__title">还没有大纲</div>
            <div className="empty-state__hint" style={{ maxWidth: 420, textAlign: "center" }}>
              写小说先有大纲。每条大纲 = 一段剧情弧，包含弧目标 + 章节任务单（爽点 / 钩子 / 字数 / 节奏）。
            </div>
            <button className="btn btn-primary" onClick={() => setShowGenModal(true)} style={{ marginTop: 16 }}>
              生成第一个大纲
            </button>
          </div>
        </div>
      )}

      {outlines.map((o) => (
        <OutlineCard
          key={o.id}
          outline={o}
          expanded={expandedArc === o.id}
          onToggle={() => setExpandedArc(expandedArc === o.id ? null : o.id)}
          onStatus={(status) => handleStatus(o, status)}
          onDelete={() => handleDelete(o)}
        />
      ))}

      {/* 生成 modal */}
      {showGenModal && (
        <div className="modal-backdrop" onClick={() => setShowGenModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>✨ 生成弧级大纲</h3>
            <p className="text-muted" style={{ fontSize: 12, marginBottom: 14 }}>
              LLM 会根据弧名称 + 弧目标 + 章节数 拆出 chapter_task 任务单。
              同 arc_id 已有大纲会被覆盖。
            </p>
            <div className="form-grid">
              <div className="field">
                <label>弧 ID</label>
                <input
                  type="number"
                  value={genForm.arc_id}
                  onChange={(e) => setGenForm({ ...genForm, arc_id: Number(e.target.value) })}
                />
              </div>
              <div className="field">
                <label>弧名称 *</label>
                <input
                  value={genForm.arc_name}
                  onChange={(e) => setGenForm({ ...genForm, arc_name: e.target.value })}
                  placeholder="如：觉醒、修真起点、对峙…"
                />
              </div>
            </div>
            <div className="field">
              <label>弧目标 *（一段话讲清这条弧要发生什么）</label>
              <textarea
                rows={3}
                value={genForm.arc_goal}
                onChange={(e) => setGenForm({ ...genForm, arc_goal: e.target.value })}
                placeholder="如：主角觉醒，获得上古传承，开始修真之路"
              />
            </div>
            <div className="form-grid">
              <div className="field">
                <label>预计章节数</label>
                <input
                  type="number"
                  min={1}
                  max={300}
                  value={genForm.arc_estimated_chapters}
                  onChange={(e) => setGenForm({ ...genForm, arc_estimated_chapters: Number(e.target.value) })}
                />
              </div>
              <div className="field">
                <label>情绪曲线</label>
                <select
                  value={genForm.emotion_curve || "上升"}
                  onChange={(e) => setGenForm({ ...genForm, emotion_curve: e.target.value })}
                >
                  <option value="上升">上升</option>
                  <option value="平稳">平稳</option>
                  <option value="起伏">起伏</option>
                  <option value="下降">下降</option>
                </select>
              </div>
            </div>
            <div className="field">
              <label>高潮描述（可选）</label>
              <input
                value={genForm.arc_climax_description || ""}
                onChange={(e) => setGenForm({ ...genForm, arc_climax_description: e.target.value })}
                placeholder="如：主角觉醒九天神脉"
              />
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
              <button type="button" className="btn" onClick={() => setShowGenModal(false)}>取消</button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleGenerate}
                disabled={generating !== null}
              >
                {generating !== null ? "生成中…" : "开始生成"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────── 单个弧卡片 ────────────────────

function OutlineCard({
  outline, expanded, onToggle, onStatus, onDelete,
}: {
  outline: OutlineOut;
  expanded: boolean;
  onToggle: () => void;
  onStatus: (status: string) => void;
  onDelete: () => void;
}) {
  const tasks = outline.outline_json || [];
  const statusBadge =
    outline.status === "approved" ? <span className="badge badge-stamp">已审批</span> :
    outline.status === "in_progress" ? <span className="badge badge-soft" style={{ background: "rgba(107,138,253,0.2)" }}>写作中</span> :
    outline.status === "done" ? <span className="badge badge-stamp">已完成</span> :
    <span className="badge badge-soft">草稿</span>;

  return (
    <div className="card mt-24">
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <span className="module-heading__index" style={{ marginRight: 0 }}>M05.{outline.arc_id}</span>
            <h3 style={{ margin: 0, fontSize: 18, fontFamily: "var(--font-display)" }}>
              {outline.arc_name}
            </h3>
            {statusBadge}
            <span className="text-faint" style={{ fontSize: 12 }}>
              · {tasks.length} 章任务 · {outline.emotion_curve}
            </span>
          </div>
          {outline.arc_goal && (
            <div className="text-muted" style={{ fontSize: 13, marginBottom: 8 }}>
              🎯 {outline.arc_goal}
            </div>
          )}
          <div className="text-faint" style={{ fontSize: 11.5 }}>
            创建于 {outline.created_at.slice(0, 10)} · 更新于 {outline.updated_at.slice(0, 10)}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
          <div style={{ display: "flex", gap: 6 }}>
            {outline.status !== "approved" && (
              <button
                className="btn btn-ghost"
                style={{ fontSize: 12 }}
                onClick={(e) => { e.stopPropagation(); onStatus("approved"); }}
              >
                ✓ 审批
              </button>
            )}
            {outline.status !== "in_progress" && outline.status === "approved" && (
              <button
                className="btn btn-ghost"
                style={{ fontSize: 12 }}
                onClick={(e) => { e.stopPropagation(); onStatus("in_progress"); }}
              >
                ✍️ 标记写作中
              </button>
            )}
            <button
              className="btn btn-ghost"
              style={{ fontSize: 12 }}
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
            >
              🗑 删除
            </button>
          </div>
        </div>
      </div>

      <button
        className="btn btn-ghost"
        style={{ marginTop: 12, fontSize: 12 }}
        onClick={onToggle}
      >
        {expanded ? "▼ 收起章节任务单" : "▶ 展开章节任务单"}
      </button>

      {expanded && (
        <div style={{ marginTop: 14 }}>
          {tasks.length === 0 ? (
            <div className="empty-state">这条弧还没生成章节任务单</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 60 }}>章</th>
                  <th style={{ width: 80 }}>定位</th>
                  <th>章节目标</th>
                  <th style={{ width: 110 }}>出场人物</th>
                  <th style={{ width: 80 }}>爽点</th>
                  <th style={{ width: 90 }}>钩子</th>
                  <th style={{ width: 80 }}>字数</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.chapter_number} className={t.is_arc_climax ? "is-climax" : ""}>
                    <td className="mono">Ch{t.chapter_number}</td>
                    <td>
                      <span className={`role-badge role-${t.chapter_role}`}>{t.chapter_role}</span>
                    </td>
                    <td>{t.chapter_goal}</td>
                    <td className="text-faint" style={{ fontSize: 12 }}>
                      {t.main_characters?.join("、") || "—"}
                    </td>
                    <td className="text-faint" style={{ fontSize: 12 }}>{t.shuang_type || "—"}</td>
                    <td className="text-faint" style={{ fontSize: 12 }}>{t.ending_hook_type || "—"}</td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{t.target_length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}