import { useEffect, useMemo, useRef, useState } from "react";
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
type SaveState = "idle" | "draft" | "saving" | "saved" | "error" | "conflict";

type LocalChapterDraft = {
  title: string;
  content: string;
  baseRevision: string;
  updatedAt: number;
};

export default function ChapterReader() {
  const { projectId, chapterNo: chapterNoStr } = useParams<{ projectId: string; chapterNo: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const chapterNo = Number(chapterNoStr);

  const [chapter, setChapter] = useState<ChapterFull | null>(null);
  const [allChapters, setAllChapters] = useState<ChapterListItem[]>([]);
  const [characters, setCharacters] = useState<ChapterCharacter[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [baseRevision, setBaseRevision] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const mountedRef = useRef(true);
  const requestRef = useRef(0);
  const saveRequestRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

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

  const draftKey = projectId && Number.isInteger(chapterNo)
    ? `novel_ai.chapter_draft.${projectId}.${chapterNo}`
    : null;
  const dirty = Boolean(chapter) && (
    editTitle !== (chapter?.title || "") || editContent !== (chapter?.content || "")
  );

  useEffect(() => {
    if (!projectId || !Number.isInteger(chapterNo) || chapterNo < 1) {
      setChapter(null);
      setLoading(false);
      setLoadError("章节号无效");
      return;
    }
    const requestId = ++requestRef.current;
    setLoading(true);
    setLoadError(null);
    setChapter(null);
    setCharacters([]);
    api.listChapters(projectId)
      .then(async (list) => {
        if (!mountedRef.current || requestRef.current !== requestId) return;
        setAllChapters(list);
        const target = list.find((c) => c.chapter_no === chapterNo);
        if (!target) {
          setLoadError(`找不到第 ${chapterNo} 章`);
          toast.error("找不到该章节", `chapter_no=${chapterNo}`);
          return;
        }
        const full = await api.getChapter(projectId, target.id);
        if (!mountedRef.current || requestRef.current !== requestId) return;
        setChapter(full);
        setEditTitle(full.title || "");
        setEditContent(full.content);
        setBaseRevision(full.revision_hash);
        setEditing(false);
        setSaveState("idle");
        if (draftKey) {
          try {
            const raw = localStorage.getItem(draftKey);
            if (raw) {
              const draft = JSON.parse(raw) as LocalChapterDraft;
              if (draft.baseRevision === full.revision_hash &&
                  (draft.title !== (full.title || "") || draft.content !== full.content)) {
                setEditTitle(draft.title);
                setEditContent(draft.content);
                setEditing(true);
                setSaveState("draft");
                toast.info("已恢复本地草稿", "服务器正文未被覆盖");
              }
            }
          } catch {
            localStorage.removeItem(draftKey);
          }
        }
        // 加载出场人物
        try {
          const chars = await api.getChapterCharacters(projectId, target.id);
          if (!mountedRef.current || requestRef.current !== requestId) return;
          setCharacters(chars);
        } catch (e) {
          // 不致命
          console.warn("getChapterCharacters failed:", e);
        }
      })
      .catch((e) => {
        if (!mountedRef.current || requestRef.current !== requestId) return;
        const msg = String(e);
        setLoadError(msg);
        toast.error("章节加载失败", msg);
      })
      .finally(() => {
        if (mountedRef.current && requestRef.current === requestId) setLoading(false);
      });
    return () => {
      if (requestRef.current === requestId) requestRef.current += 1;
    };
  }, [projectId, chapterNo, reloadKey, toast, draftKey]);

  useEffect(() => {
    if (!editing || !dirty || !draftKey || !baseRevision) return;
    const draft: LocalChapterDraft = {
      title: editTitle,
      content: editContent,
      baseRevision,
      updatedAt: Date.now(),
    };
    localStorage.setItem(draftKey, JSON.stringify(draft));
    setSaveState((current) => current === "conflict" ? current : "draft");
  }, [editing, dirty, draftKey, baseRevision, editTitle, editContent]);

  async function saveEdits(options: { silent?: boolean } = {}) {
    if (!projectId || !chapter || !dirty || saveState === "saving" || saveState === "conflict") return;
    const requestId = ++saveRequestRef.current;
    setSaveState("saving");
    try {
      const updated = await api.updateChapter(projectId, chapter.id, {
        title: editTitle.trim() || null,
        content: editContent,
        expected_revision_hash: baseRevision,
      });
      if (!mountedRef.current || saveRequestRef.current !== requestId) return;
      setChapter((current) => current ? { ...current, ...updated } : current);
      setEditTitle(updated.title || "");
      setEditContent(updated.content);
      setBaseRevision(updated.revision_hash);
      setSaveState("saved");
      if (draftKey) localStorage.removeItem(draftKey);
      if (!options.silent) {
        toast.success("章节已保存", updated.engine_file_synced ? "数据库、引擎正文与检索索引已同步" : "数据库与检索索引已同步");
      }
    } catch (error) {
      if (!mountedRef.current || saveRequestRef.current !== requestId) return;
      const message = String(error);
      if (message.includes("chapter_revision_conflict")) {
        setSaveState("conflict");
        toast.error("保存冲突", "服务器版本已变化，请重新加载；本地草稿仍保留");
      } else {
        setSaveState("error");
        if (!options.silent) toast.error("保存失败", message);
      }
    }
  }

  useEffect(() => {
    if (!editing || !dirty || saveState === "conflict" || saveState === "saving") return;
    const timer = window.setTimeout(() => { void saveEdits({ silent: true }); }, 1500);
    return () => window.clearTimeout(timer);
    // saveEdits intentionally reads the latest render state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, dirty, editTitle, editContent, baseRevision, saveState]);

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  function replaceAll() {
    if (!findText) return;
    const matches = editContent.split(findText).length - 1;
    if (matches <= 0) {
      toast.info("未找到匹配文本");
      return;
    }
    setEditContent(editContent.split(findText).join(replaceText));
    toast.info(`已替换 ${matches} 处`, "等待自动保存或点击保存");
  }

  const sortedChapters = useMemo(
    () => [...allChapters].sort((a, b) => a.chapter_no - b.chapter_no),
    [allChapters]
  );

  const currentIdx = sortedChapters.findIndex((c) => c.chapter_no === chapterNo);
  const prevChapter = currentIdx > 0 ? sortedChapters[currentIdx - 1] : null;
  const nextChapter = currentIdx >= 0 && currentIdx < sortedChapters.length - 1 ? sortedChapters[currentIdx + 1] : null;

  function goToChapter(no: number) {
    if (!projectId) return;
    if (dirty && !confirm("当前章节还有未保存修改，仍要离开吗？本地草稿会保留。")) return;
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
          <div className="banner banner-danger" role="alert">
            <span>章节不存在或加载失败{loadError ? `：${loadError}` : ""}</span>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                setReloadKey((value) => value + 1);
              }}
              disabled={loading}
              aria-label="重试加载章节"
            >
              重试
            </button>
          </div>
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
        <Link
          to={`/projects/${projectId}/chapters`}
          className="reader-back"
          onClick={(event) => {
            if (dirty && !confirm("当前章节还有未保存修改，仍要离开吗？本地草稿会保留。")) {
              event.preventDefault();
            }
          }}
        >
          ← 章节列表
        </Link>
        <div className="reader-topbar__center">
          第 {chapter.chapter_no} 章 · {allChapters.length} 章中
        </div>
        <div className="reader-topbar__right">
          <span className={`reader-save-state reader-save-state--${saveState}`}>
            {saveState === "saving" ? "保存中…" : saveState === "saved" ? "已保存" :
              saveState === "draft" ? "本地草稿" : saveState === "conflict" ? "版本冲突" :
              saveState === "error" ? "保存失败" : ""}
          </span>
          <button
            type="button"
            className={`btn btn-sm ${editing ? "btn-primary" : "btn-ghost"}`}
            onClick={() => {
              if (editing && dirty && !confirm("退出编辑会保留本地草稿，继续吗？")) return;
              setEditing((value) => !value);
            }}
          >
            {editing ? "阅读模式" : "编辑"}
          </button>
          {editing && (
            <button
              type="button"
              className="btn btn-sm"
              disabled={!dirty || saveState === "saving" || saveState === "conflict"}
              onClick={() => void saveEdits()}
            >
              保存
            </button>
          )}
          <button
            type="button"
            className="reader-icon-btn"
            onClick={() => setTocOpen((v) => !v)}
            title={tocOpen ? "隐藏目录" : "显示目录"}
            aria-label={tocOpen ? "隐藏目录" : "显示目录"}
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
                  type="button"
                  key={c.id}
                  className={`reader-toc__item ${c.chapter_no === chapterNo ? "is-current" : ""}`}
                  onClick={() => goToChapter(c.chapter_no)}
                  title={c.title || `第${c.chapter_no}章`}
                  aria-label={`跳转到 ${c.title || `第${c.chapter_no}章`}`}
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
              {/* 2026-07-23 修复（问题 #8 步骤 E）：title 守卫。
                  后端历史脏数据：title 字段可能是 JSON 字面量（`{"title": "...", "body":`），
                  或 LLM 漂移的长串。展示前检测 + 降级，避免读者看到 JSON 包装。 */}
              {editing ? (
                <input
                  className="reader-editor__title"
                  value={editTitle}
                  maxLength={200}
                  onChange={(event) => setEditTitle(event.target.value)}
                  placeholder="章节标题"
                  aria-label="章节标题"
                />
              ) : (
                <h1 className="reader-header__title">
                  {(() => {
                    const t = (chapter.title || "").trim();
                    if (!t) return "（无标题）";
                    if (t.startsWith("{") || t.length > 30) {
                      return "（标题待生成）";
                    }
                    return t;
                  })()}
                </h1>
              )}
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

            {editing ? (
              <div className="reader-editor">
                <div className="reader-editor__find">
                  <input
                    value={findText}
                    onChange={(event) => setFindText(event.target.value)}
                    placeholder="查找"
                    aria-label="查找文本"
                  />
                  <input
                    value={replaceText}
                    onChange={(event) => setReplaceText(event.target.value)}
                    placeholder="替换为"
                    aria-label="替换文本"
                  />
                  <button type="button" className="btn btn-sm" onClick={replaceAll} disabled={!findText}>
                    全部替换
                  </button>
                  <span>{editContent.length.toLocaleString()} 字</span>
                </div>
                {saveState === "conflict" && (
                  <div className="banner banner-danger">
                    服务器章节已更新。你的本地草稿仍保留；请先复制需要保留的内容，再重新加载。
                    <button type="button" className="btn btn-sm" onClick={() => setReloadKey((value) => value + 1)}>
                      重新加载
                    </button>
                  </div>
                )}
                <textarea
                  className="reader-editor__content"
                  value={editContent}
                  onChange={(event) => setEditContent(event.target.value)}
                  spellCheck={false}
                  aria-label="章节正文"
                />
              </div>
            ) : (
              <div className="reader-body">
                {chapter.content.split(/\n\n+/).map((p, i) => (
                  <p key={i} className="reader-paragraph">{p}</p>
                ))}
              </div>
            )}

            {/* 底部上下章导航 */}
            <nav className="reader-pager">
              {prevChapter ? (
                <button
                  type="button"
                  className="reader-pager__btn"
                  onClick={() => goToChapter(prevChapter.chapter_no)}
                  aria-label={`上一章 ${prevChapter.title || `第${prevChapter.chapter_no}章`}`}
                >
                  <span className="reader-pager__label">← 上一章</span>
                  <span className="reader-pager__title">Ch{prevChapter.chapter_no} · {prevChapter.title || "（无标题）"}</span>
                </button>
              ) : (
                <div className="reader-pager__btn reader-pager__btn--disabled">
                  <span className="reader-pager__label">已是第一章</span>
                </div>
              )}
              {nextChapter ? (
                <button
                  type="button"
                  className="reader-pager__btn"
                  onClick={() => goToChapter(nextChapter.chapter_no)}
                  aria-label={`下一章 ${nextChapter.title || `第${nextChapter.chapter_no}章`}`}
                >
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
      <button
        type="button"
        className="reader-icon-btn"
        onClick={() => setOpen(!open)}
        title="阅读设置"
        aria-label="阅读设置"
      >Aa</button>
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
                  type="button"
                  key={t}
                  className={`reader-theme-btn reader-theme-btn--${t} ${theme === t ? "is-active" : ""}`}
                  onClick={() => setTheme(t)}
                  title={t}
                  aria-label={`切换主题 ${t}`}
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
