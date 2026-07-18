import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ChapterFull, ChapterListItem, ChapterCharacter } from "../types";
import { useToast } from "../components/Toast";

/**
 * ChapterReader — 章节阅读器
 *
 * 替代之前的 Dialog 弹窗模式。独立的「阅读模式」页面：
 *  - URL: /projects/:id/chapter/:chapterNo
 *  - 侧栏 TOC：所有章节列表，当前章节高亮
 *  - 上下章导航（顶部 + 底部）
 *  - 阅读设置：字号 / 行高 / 主题（light / dark / sepia）
 *  - 出场人物侧边卡片
 *
 * 设计目标：让 300+ 章的长篇小说有真正的「读」体验，而不是点开弹窗看几秒就关。
 */
type Theme = "dark" | "light" | "sepia";

export default function ChapterReader() {
  const { projectId, chapterNo: chapterNoStr } = useParams<{ projectId: string; chapterNo: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const chapterNo = Number(chapterNoStr);

  const [chapter, setChapter] = useState<ChapterFull | null>(null);
  const [allChapters, setAllChapters] = useState<ChapterListItem[]>([]);
  const [characters, setCharacters] = useState<ChapterCharacter[]>([]);
  const [loading, setLoading] = useState(true);

  // 阅读设置（持久化到 localStorage）
  const [fontSize, setFontSize] = useState<number>(() => {
    const v = localStorage.getItem("reader.fontSize");
    return v ? Number(v) : 16;
  });
  const [lineHeight, setLineHeight] = useState<number>(() => {
    const v = localStorage.getItem("reader.lineHeight");
    return v ? Number(v) : 1.9;
  });
  const [theme, setTheme] = useState<Theme>(() => {
    return (localStorage.getItem("reader.theme") as Theme) || "dark";
  });
  const [tocOpen, setTocOpen] = useState<boolean>(() => {
    return localStorage.getItem("reader.tocOpen") !== "false";
  });

  useEffect(() => {
    localStorage.setItem("reader.fontSize", String(fontSize));
  }, [fontSize]);
  useEffect(() => {
    localStorage.setItem("reader.lineHeight", String(lineHeight));
  }, [lineHeight]);
  useEffect(() => {
    localStorage.setItem("reader.theme", theme);
  }, [theme]);
  useEffect(() => {
    localStorage.setItem("reader.tocOpen", String(tocOpen));
  }, [tocOpen]);

  useEffect(() => {
    if (!projectId || !chapterNo) return;
    setLoading(true);
    Promise.all([
      api.listChapters(projectId),
      // 通过列表查找 chapter_id，再获取完整内容
    ])
      .then(async ([list]) => {
        setAllChapters(list);
        const target = list.find((c) => c.chapter_no === chapterNo);
        if (!target) {
          toast.error("找不到该章节", `chapter_no=${chapterNo}`);
          return;
        }
        const full = await api.getChapter(projectId, target.id);
        setChapter(full);
        // 加载出场人物
        try {
          const chars = await api.getChapterCharacters(projectId, target.id);
          setCharacters(chars);
        } catch (e) {
          // 不致命
          console.warn("getChapterCharacters failed:", e);
        }
      })
      .catch((e) => toast.error("章节加载失败", String(e)))
      .finally(() => setLoading(false));
  }, [projectId, chapterNo, toast]);

  const sortedChapters = useMemo(
    () => [...allChapters].sort((a, b) => a.chapter_no - b.chapter_no),
    [allChapters]
  );

  const currentIdx = sortedChapters.findIndex((c) => c.chapter_no === chapterNo);
  const prevChapter = currentIdx > 0 ? sortedChapters[currentIdx - 1] : null;
  const nextChapter = currentIdx >= 0 && currentIdx < sortedChapters.length - 1 ? sortedChapters[currentIdx + 1] : null;

  function goToChapter(no: number) {
    if (!projectId) return;
    navigate(`/projects/${projectId}/chapter/${no}`);
  }

  if (loading) {
    return (
      <div className={`reader-page reader-theme-${theme}`}>
        <div className="loading-text">加载章节…</div>
      </div>
    );
  }

  if (!chapter) {
    return (
      <div className={`reader-page reader-theme-${theme}`}>
        <div className="card">
          <div className="banner banner-danger">章节不存在或加载失败</div>
          <Link to={`/projects/${projectId}/chapters`} className="btn btn-primary" style={{ marginTop: 12 }}>
            ← 返回章节列表
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className={`reader-page reader-theme-${theme}`}>
      {/* 顶部导航栏 */}
      <div className="reader-topbar">
        <Link to={`/projects/${projectId}/chapters`} className="reader-back">
          ← 章节列表
        </Link>
        <div className="reader-topbar__center">
          第 {chapter.chapter_no} 章 · {allChapters.length} 章中
        </div>
        <div className="reader-topbar__right">
          <button
            className="reader-icon-btn"
            onClick={() => setTocOpen((v) => !v)}
            title={tocOpen ? "隐藏目录" : "显示目录"}
          >
            ☰
          </button>
          <ReaderSettings
            fontSize={fontSize} setFontSize={setFontSize}
            lineHeight={lineHeight} setLineHeight={setLineHeight}
            theme={theme} setTheme={setTheme}
          />
        </div>
      </div>

      <div className={`reader-layout ${tocOpen ? "with-toc" : "no-toc"}`}>
        {/* 侧栏 TOC */}
        {tocOpen && (
          <aside className="reader-toc">
            <div className="reader-toc__head">章节目录</div>
            <div className="reader-toc__list">
              {sortedChapters.map((c) => (
                <button
                  key={c.id}
                  className={`reader-toc__item ${c.chapter_no === chapterNo ? "is-current" : ""}`}
                  onClick={() => goToChapter(c.chapter_no)}
                  title={c.title || `第${c.chapter_no}章`}
                >
                  <span className="reader-toc__no">Ch{c.chapter_no}</span>
                  <span className="reader-toc__title">{c.title || "（无标题）"}</span>
                </button>
              ))}
            </div>
          </aside>
        )}

        {/* 主阅读区 */}
        <main className="reader-main" style={{ fontSize: `${fontSize}px`, lineHeight: lineHeight }}>
          <article className="reader-article">
            <header className="reader-header">
              <div className="reader-header__no">第 {chapter.chapter_no} 章</div>
              <h1 className="reader-header__title">{chapter.title || "（无标题）"}</h1>
              <div className="reader-header__meta">
                {chapter.content.length.toLocaleString()} 字 ·{" "}
                {chapter.created_at ? new Date(chapter.created_at).toLocaleDateString() : "未知日期"}
              </div>
              {characters.length > 0 && (
                <div className="reader-header__chars">
                  <span className="text-faint" style={{ fontSize: 12, marginRight: 6 }}>出场人物：</span>
                  {characters.map((c) => (
                    <span key={c.id} className="reader-chip">{c.character_name}</span>
                  ))}
                </div>
              )}
            </header>

            <div className="reader-body">
              {chapter.content.split(/\n\n+/).map((p, i) => (
                <p key={i} className="reader-paragraph">{p}</p>
              ))}
            </div>

            {/* 底部上下章导航 */}
            <nav className="reader-pager">
              {prevChapter ? (
                <button className="reader-pager__btn" onClick={() => goToChapter(prevChapter.chapter_no)}>
                  <span className="reader-pager__label">← 上一章</span>
                  <span className="reader-pager__title">Ch{prevChapter.chapter_no} · {prevChapter.title || "（无标题）"}</span>
                </button>
              ) : (
                <div className="reader-pager__btn reader-pager__btn--disabled">
                  <span className="reader-pager__label">已是第一章</span>
                </div>
              )}
              {nextChapter ? (
                <button className="reader-pager__btn" onClick={() => goToChapter(nextChapter.chapter_no)}>
                  <span className="reader-pager__label">下一章 →</span>
                  <span className="reader-pager__title">Ch{nextChapter.chapter_no} · {nextChapter.title || "（无标题）"}</span>
                </button>
              ) : (
                <div className="reader-pager__btn reader-pager__btn--disabled">
                  <span className="reader-pager__label">已是最后一章</span>
                </div>
              )}
            </nav>
          </article>
        </main>
      </div>

      {/* 阅读进度条 */}
      <ReadingProgress />
    </div>
  );
}

// ──────────────────── 阅读设置下拉 ────────────────────

function ReaderSettings({
  fontSize, setFontSize, lineHeight, setLineHeight, theme, setTheme,
}: {
  fontSize: number; setFontSize: (n: number) => void;
  lineHeight: number; setLineHeight: (n: number) => void;
  theme: Theme; setTheme: (t: Theme) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="reader-settings">
      <button className="reader-icon-btn" onClick={() => setOpen(!open)} title="阅读设置">Aa</button>
      {open && (
        <div className="reader-settings__panel" onMouseLeave={() => setOpen(false)}>
          <div className="reader-settings__row">
            <label>字号</label>
            <input
              type="range" min={12} max={22} step={1}
              value={fontSize}
              onChange={(e) => setFontSize(Number(e.target.value))}
            />
            <span className="reader-settings__val">{fontSize}px</span>
          </div>
          <div className="reader-settings__row">
            <label>行距</label>
            <input
              type="range" min={1.4} max={2.4} step={0.1}
              value={lineHeight}
              onChange={(e) => setLineHeight(Number(e.target.value))}
            />
            <span className="reader-settings__val">{lineHeight.toFixed(1)}</span>
          </div>
          <div className="reader-settings__row">
            <label>主题</label>
            <div className="reader-settings__themes">
              {(["dark", "light", "sepia"] as Theme[]).map((t) => (
                <button
                  key={t}
                  className={`reader-theme-btn reader-theme-btn--${t} ${theme === t ? "is-active" : ""}`}
                  onClick={() => setTheme(t)}
                  title={t}
                >
                  {t === "dark" ? "🌙" : t === "light" ? "☀️" : "📖"}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────── 阅读进度条 ────────────────────

function ReadingProgress() {
  const [pct, setPct] = useState(0);
  useEffect(() => {
    function update() {
      const docHeight = document.documentElement.scrollHeight - window.innerHeight;
      const scrolled = window.scrollY;
      setPct(docHeight > 0 ? Math.min(100, (scrolled / docHeight) * 100) : 0);
    }
    window.addEventListener("scroll", update);
    update();
    return () => window.removeEventListener("scroll", update);
  }, []);
  return (
    <div className="reader-progress" aria-hidden="true">
      <div className="reader-progress__bar" style={{ width: `${pct}%` }} />
    </div>
  );
}