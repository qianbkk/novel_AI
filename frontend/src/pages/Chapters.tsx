import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  ChapterListItem, ChapterFull, ChapterCharacter,
  Character, ChapterSearchResult, RepetitionWarning,
} from "../types";
import { useReveal } from "../hooks/useReveal";
import { useToast } from "../components/Toast";
import { Dialog } from "../components/Dialog";

// 衔接锁四个字段：场景布置 / 角色登场 / 物品状态 / 前章收尾
function deriveSceneLayout(text: string): string {
  return text.split(/[。！？!?]/)[0]?.slice(0, 36) || "—";
}

function deriveItemState(text: string): string {
  const itemKeywords = ["灵石", "剑", "玉佩", "丹药", "符", "丹", "阵", "卷轴", "宝", "法器", "丹炉"];
  const matched = itemKeywords.filter((k) => text.includes(k));
  return matched.length > 0 ? matched.join("、") : "—";
}

export default function Chapters() {
  const { projectId } = useParams<{ projectId: string }>();
  const [chapters, setChapters] = useState<ChapterListItem[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);

  const [chapterNo, setChapterNo] = useState(1);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [repetitionWarnings, setRepetitionWarnings] = useState<RepetitionWarning[]>([]);
  const [lastSavedChapterId, setLastSavedChapterId] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [characterId, setCharacterId] = useState("");
  const [searchResults, setSearchResults] = useState<ChapterSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);

  // 单章详情抽屉：缓存展开时加载的完整章节内容
  const [chapterDetail, setChapterDetail] = useState<ChapterFull | null>(null);
  const [chapterDetailLoading, setChapterDetailLoading] = useState(false);

  const [expandedLock, setExpandedLock] = useState<string | null>(null);
  const [charactersByChapter, setCharactersByChapter] = useState<Record<string, ChapterCharacter[]>>({});

  const rootRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();
  useReveal(rootRef);

  async function refreshChapters() {
    if (!projectId) return;
    try {
      setChapters(await api.listChapters(projectId));
    } catch (e) {
      toast.error("刷新章节列表失败", String(e));
    }
  }

  useEffect(() => {
    if (!projectId) return;
    refreshChapters();
    // getWorldbuildResult 失败 → characters 留空不影响主功能（章节写入不需要），
    // 但仍然 toast.warn 让用户知道「角色图谱没拿到」
    api.getWorldbuildResult(projectId)
      .then((r) => setCharacters(r.characters))
      .catch((e) => toast.warn("角色图谱加载失败", String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // 展开章节衔接锁时，按需加载出场人物（真图谱）
  useEffect(() => {
    if (!expandedLock || !projectId) return;
    if (charactersByChapter[expandedLock]) return;
    api.getChapterCharacters(projectId, expandedLock)
      .then((chars) => {
        setCharactersByChapter((prev) => ({ ...prev, [expandedLock]: chars }));
      })
      .catch((e) => {
        toast.warn("出场人物加载失败", String(e));
        setCharactersByChapter((prev) => ({ ...prev, [expandedLock]: [] }));
      });
  }, [expandedLock, projectId, charactersByChapter]);

  // 打开单章详情
  async function openChapterDetail(chapterId: string) {
    if (!projectId) return;
    setChapterDetailLoading(true);
    try {
      const full = await api.getChapter(projectId, chapterId);
      setChapterDetail(full);
    } catch (e) {
      toast.error("章节详情加载失败", String(e));
    } finally {
      setChapterDetailLoading(false);
    }
  }

  async function handleSave() {
    if (!projectId || !content.trim()) return;
    setSaving(true);
    setRepetitionWarnings([]);
    try {
      const res = await api.createChapter(projectId, { chapter_no: chapterNo, title, content });
      setRepetitionWarnings(res.repetition_warnings);
      setLastSavedChapterId(res.chapter_id);
      setTitle("");
      setContent("");
      setChapterNo((n) => n + 1);
      await refreshChapters();
      toast.success(`第 ${chapterNo - 1} 章已保存`, `共 ${content.length.toLocaleString()} 字`);
    } catch (e) {
      toast.error("保存失败", String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleSearch() {
    if (!projectId || !query.trim()) return;
    setSearching(true);
    try {
      setSearchResults(await api.searchChapters(projectId, query, characterId || undefined));
    } catch (e) {
      toast.error("搜索失败", String(e));
      setSearchResults([]);  // 让 UI 显式显示「无结果」而不是停留在旧结果
    } finally {
      setSearching(false);
    }
  }

  function chapterLabel(id: string) {
    const c = chapters.find((ch) => ch.id === id);
    return c ? `第${c.chapter_no}章 ${c.title || ""}` : id;
  }

  const chaptersDesc = useMemo(() => [...chapters].sort((a, b) => b.chapter_no - a.chapter_no), [chapters]);

  return (
    <div ref={rootRef}>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">章节管理</h1>
          <div className="page-header__sub">
            {chapters.length > 0
              ? `已存 ${chapters.length} 章 · 共 ${chapters.reduce((a, c) => a + c.word_count, 0).toLocaleString()} 字`
              : "从写作控制台写入后会自动入库"}
          </div>
        </div>
      </div>

      <div className="card">
        <h3 className="card__title">手动新增一章</h3>
        <div className="form-grid" style={{ marginBottom: 0 }}>
          <div className="field">
            <label>章节号</label>
            <input
              type="number"
              value={chapterNo}
              onChange={(e) => setChapterNo(Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label>标题</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="第N章 标题"
            />
          </div>
        </div>
        <div className="field">
          <div className="flex-between" style={{ marginBottom: 6 }}>
            <label style={{ marginBottom: 0 }}>正文</label>
            <span className="text-mono text-faint tabular-nums" style={{ fontSize: 11.5 }}>
              {content.length.toLocaleString()} 字
            </span>
          </div>
          <textarea
            rows={12}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="粘贴或输入这一章的正文…保存后会自动标记出场人物、embed 全文并跑一次重复度检测"
            style={{ fontFamily: "var(--font-body)", lineHeight: 1.8 }}
          />
        </div>

        {repetitionWarnings.length > 0 && (
          <div className="banner banner-warn">
            检测到与 {repetitionWarnings.length} 章高度相似（语义重复度 ≥ 0.85）：
            {repetitionWarnings.map((w) => (
              <div key={w.compared_chapter_id} className="mono" style={{ marginTop: 4 }}>
                {chapterLabel(w.compared_chapter_id)} · 相似度 {w.similarity}
              </div>
            ))}
          </div>
        )}

        {lastSavedChapterId && !repetitionWarnings.length && (
          <div className="banner banner-success" style={{ fontSize: 12 }}>
            ✅ 已保存第{chapterNo - 1}章 ·{" "}
            <a onClick={() => openChapterDetail(lastSavedChapterId)}
               style={{ cursor: "pointer", textDecoration: "underline" }}>
              查看完整正文
            </a>
          </div>
        )}

        <button
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving || !content.trim()}
        >
          {saving ? "保存中…" : "保存这一章"}
        </button>
      </div>

      <div className="card mt-24">
        <h3 className="card__title">语义检索</h3>
        <div className="form-grid" style={{ alignItems: "end" }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>检索意图</label>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="如：重生回到答辩现场"
            />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>角色过滤</label>
            <select value={characterId} onChange={(e) => setCharacterId(e.target.value)}>
              <option value="">不限角色</option>
              {characters.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="button-row" style={{ marginTop: 12 }}>
          <button
            className="btn btn-primary"
            onClick={handleSearch}
            disabled={searching || !query.trim()}
          >
            {searching ? "检索中…" : "开始检索"}
          </button>
        </div>

        {searchResults && (
          <div className="mt-24">
            {searchResults.length === 0 ? (
              <div className="empty-state">没有匹配的章节。</div>
            ) : (
              searchResults.map((r) => (
                <div className="entity-card" key={r.chapter_id}>
                  <span className="entity-card__name font-display">{chapterLabel(r.chapter_id)}</span>
                  <span className="entity-card__meta mono">相似度 {r.similarity}</span>
                  <div className="entity-card__desc">{r.snippet}…</div>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* ============ 已保存章节 + 衔接锁 ============ */}
      <div className="card mt-24" style={{ position: "relative", overflow: "hidden" }}>
        <div className="ink-drop-bg ink-drop-bg--soft" aria-hidden="true">
          <svg viewBox="0 0 600 300" preserveAspectRatio="xMidYMid slice">
            <defs>
              <radialGradient id="chapter-ink" cx="80%" cy="20%" r="60%">
                <stop offset="0%" stopColor="#6B8AFD" stopOpacity="0.18" />
                <stop offset="100%" stopColor="#6B8AFD" stopOpacity="0" />
              </radialGradient>
            </defs>
            <circle cx="500" cy="40" r="160" fill="url(#chapter-ink)" />
            <path
              d="M 30 280 q 18 -22 0 -44 q -18 22 0 44 z"
              fill="#E06C5F"
              opacity="0.10"
            />
          </svg>
        </div>
        <h3 className="card__title">已保存章节 · 衔接锁</h3>
        <div className="text-muted" style={{ fontSize: 11.5, marginTop: -10, marginBottom: 12 }}>
          点击章节号可展开"章节衔接锁"：场景布置 / 角色登场（来自真实图谱） / 物品状态 / 前章收尾
        </div>
        {chapters.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state__icon" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="1.5"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
              </svg>
            </div>
            <div className="empty-state__title">还没有章节</div>
            <div className="empty-state__hint">
              从写作控制台点「写 N 章」，写完会自动出现在这里
            </div>
          </div>
        ) : (
          chaptersDesc.map((c) => {
            const prev = chapters.find((x) => x.chapter_no === c.chapter_no - 1);
            const text = c.content_preview || "";
            const sceneLayout = deriveSceneLayout(text);
            const itemState = deriveItemState(text);
            const prevCliff = prev ? (prev.content_preview || "").slice(-30) : "—";
            const hasPrevChapter = c.chapter_no > 1;
            const isOpen = expandedLock === c.id;
            const realChars = charactersByChapter[c.id];
            const characterNames = realChars?.map((cc) => cc.character_name).filter(Boolean) as string[] | undefined;
            return (
              <details
                key={c.id}
                className="lock-disclosure reveal"
                open={isOpen}
                onToggle={(e) => {
                  const target = e.currentTarget;
                  setExpandedLock(target.open ? c.id : null);
                }}
              >
                <summary className={`chapter-row ${isOpen ? "is-locked" : ""}`}>
                  <span className="chapter-row__no">第{c.chapter_no}章</span>
                  <div className="chapter-row__title">{c.title || "（无标题）"}</div>
                  <div className="chapter-row__preview">{c.content_preview}…</div>
                  <div className="chapter-row__meta">{c.word_count.toLocaleString()} 字</div>
                </summary>
                <span className="lock-mark" />
                <div className="conn-lock" style={{ margin: "0 0 14px 56px" }}>
                  <div className="conn-lock__pane">
                    <div className="conn-lock__head">场景布置</div>
                    <ul className="conn-lock__list">
                      <li className="conn-lock__item">{sceneLayout}</li>
                    </ul>
                  </div>
                  <div className="conn-lock__pane">
                    <div className="conn-lock__head">角色登场 / 离场</div>
                    <ul className="conn-lock__list">
                      {characterNames === undefined ? (
                        <li className="conn-lock__item conn-lock__item--empty">加载中…</li>
                      ) : characterNames.length > 0 ? (
                        characterNames.map((n) => (
                          <li className="conn-lock__item" key={n}>{n}</li>
                        ))
                      ) : (
                        <li className="conn-lock__item conn-lock__item--empty">未识别到已知角色（可能为本章首次登场）</li>
                      )}
                    </ul>
                  </div>
                  <div className="conn-lock__pane">
                    <div className="conn-lock__head">物品关联状态</div>
                    <ul className="conn-lock__list">
                      <li className="conn-lock__item">{itemState}</li>
                    </ul>
                  </div>
                  <div className="conn-lock__pane">
                    <div className="conn-lock__head">前章收尾 / 后章悬念</div>
                    <ul className="conn-lock__list">
                      <li className={`conn-lock__item ${!hasPrevChapter ? "conn-lock__item--empty" : ""}`}>
                        {hasPrevChapter ? prevCliff : "（本书第一章，无前章）"}
                      </li>
                    </ul>
                  </div>
                </div>
              </details>
            );
          })
        )}
      </div>

      {/* 单章详情 Dialog */}
      <Dialog
        open={!!chapterDetail || chapterDetailLoading}
        onClose={() => setChapterDetail(null)}
        title={chapterDetail ? `第 ${chapterDetail.chapter_no} 章 · ${chapterDetail.title || "（无标题）"}` : "加载中…"}
        sub={chapterDetail ? `${chapterDetail.content.length.toLocaleString()} 字 · ${chapterDetail.created_at}` : undefined}
        actions={
          <button className="btn" onClick={() => setChapterDetail(null)}>关闭</button>
        }
      >
        {chapterDetailLoading && (
          <div className="loading-text">加载章节详情…</div>
        )}
        {chapterDetail && !chapterDetailLoading && (
          <>
            {chapterDetail.characters.length > 0 && (
              <div style={{ marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                <span className="text-faint" style={{ fontSize: 12 }}>出场人物：</span>
                {chapterDetail.characters.map((cc) => (
                  <span key={cc.id} className="legislation-card__chip">
                    {cc.character_name}
                    {cc.character_role ? ` · ${cc.character_role}` : ""}
                  </span>
                ))}
              </div>
            )}
            <pre className="log-console" style={{
              maxHeight: "60vh", whiteSpace: "pre-wrap",
              fontFamily: "var(--font-body)", lineHeight: 1.8, fontSize: 13.5,
            }}>{chapterDetail.content}</pre>
          </>
        )}
      </Dialog>
    </div>
  );
}
