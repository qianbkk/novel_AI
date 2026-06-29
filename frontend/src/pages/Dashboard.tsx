import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ChapterListItem, Project } from "../types";

function statusBadge(status: Project["status"]) {
  if (status === "ready") return <span className="badge-stamp">已就绪</span>;
  if (status === "worldbuilding") return <span className="badge-soft">构建中</span>;
  return <span className="badge-draft">草稿</span>;
}

// 把章节列表压缩成"三道记忆防线"的可视深度
// L1 主线记忆：plot_skeleton 长度 -> 弧段数
// L2 章节衔接锁：已有章节数（每章都是一个衔接锁）
// L3 压缩存储：累计字数 / 10000 的对数，约等于长篇规模
function memoryDepth(p: Project, chapters: ChapterListItem[]) {
  const l1 = p.status === "ready" ? 5 : 1;        // 主线层是否已建立
  const l2 = Math.min(12, chapters.length);        // 衔接锁数量
  const words = chapters.reduce((a, c) => a + c.word_count, 0);
  const l3 = Math.min(12, Math.floor(Math.log10(Math.max(1, words)) * 3) + 1);
  return { l1, l2, l3 };
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [chapterMap, setChapterMap] = useState<Record<string, ChapterListItem[]>>({});
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .listProjects()
      .then(async (ps) => {
        setProjects(ps);
        // 并行拉取每个项目的章节，构建最近 3 章预览
        const entries = await Promise.all(
          ps.map(async (p) => {
            try {
              const chs = await api.listChapters(p.id);
              return [p.id, chs] as const;
            } catch {
              return [p.id, [] as ChapterListItem[]] as const;
            }
          }),
        );
        setChapterMap(Object.fromEntries(entries));
      })
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">我的项目</h1>
          <div className="page-header__sub">
            {projects
              ? `共 ${projects.length} 个项目 · 每张卡片都是一座落笔的小作坊`
              : "加载中…"}
          </div>
        </div>
        <div className="page-header__actions">
          <button
            className="btn btn-primary"
            onClick={() => navigate("/new")}
          >
            + 新建小说
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-danger">
          {error} — 后端没起来？默认地址{" "}
          <span className="text-mono">http://localhost:8123</span>
        </div>
      )}

      {projects && projects.length === 0 && (
        <div className="card">
          <div className="empty-state">
            还没有项目
            <div className="empty-state__hint">
              点右上角"新建小说"，填个标题和题材，从世界构建开始
            </div>
          </div>
        </div>
      )}

      {projects && projects.length > 0 && (
        <div className="grid-cards">
          {projects.map((p) => {
            const chs = chapterMap[p.id] || [];
            const recent = chs.slice(-3).reverse();
            const mem = memoryDepth(p, chs);
            const totalWords = chs.reduce((a, c) => a + c.word_count, 0);
            // 弧进度 = 已写章节 / 长篇目标 (粗略用 200 章当 200 万字基准)
            const arcPct = Math.min(100, Math.round((chs.length / 200) * 100));
            return (
              <div
                key={p.id}
                className="project-card"
                onClick={() =>
                  navigate(
                    p.status === "ready"
                      ? `/projects/${p.id}/bridge`
                      : `/projects/${p.id}/worldbuild`,
                  )
                }
              >
                <div className="project-card__title">
                  {p.title || "未命名小说"}
                </div>
                <div className="project-card__meta">
                  {p.genre || "未分类"}
                  {p.audience ? ` · ${p.audience}` : ""}
                </div>

                {/* 三道记忆防线 · 缩略视图 */}
                <div className="memory-stack" style={{ marginTop: 10, gap: 4 }}>
                  <div className="memory-row memory-row--l1" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">L1</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      主线记忆 · {mem.l1}/5 弧段
                    </span>
                    <span className="memory-row__count">{p.status === "ready" ? "已建立" : "草拟中"}</span>
                  </div>
                  <div className="memory-row memory-row--l2" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">L2</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      衔接锁 · 已写 {chs.length} 章
                    </span>
                    <span className="memory-row__count">{mem.l2}/12</span>
                  </div>
                  <div className="memory-row memory-row--l3" style={{ padding: "6px 10px 6px 12px" }}>
                    <span className="memory-row__layer">L3</span>
                    <span className="memory-row__title" style={{ fontSize: 11.5 }}>
                      压缩存储 · {totalWords.toLocaleString()} 字
                    </span>
                    <span className="memory-row__count">深度 {mem.l3}/12</span>
                  </div>
                </div>

                {/* 弧进度条 */}
                <div className="project-card__progress">
                  <div className="arc-pill" style={{ marginBottom: 4 }}>
                    <span>弧进度</span>
                    <span className="arc-pill__bar"><span style={{ transform: `scaleX(${arcPct / 100})` }} /></span>
                    <span>{arcPct}%</span>
                  </div>
                  <div className="progress-track" style={{ height: 3, margin: 0 }}>
                    <div className="progress-fill" style={{ width: `${arcPct}%` }} />
                  </div>
                </div>

                {/* 最近三章预览 */}
                {recent.length > 0 && (
                  <div className="last-chapters">
                    {recent.map((c) => (
                      <div className="last-chapter-line" key={c.id}>
                        <span className="last-chapter-line__no">第{c.chapter_no}章</span>
                        <span className="last-chapter-line__title">{c.title || "（无标题）"}</span>
                        <span className="last-chapter-line__preview">{c.content_preview}</span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="project-card__foot" style={{ marginTop: 14 }}>
                  {statusBadge(p.status)}
                  <span className="text-faint text-mono">{p.id.slice(0, 8)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
