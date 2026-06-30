import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  ChapterListItem, ChapterFull, ChapterCharacter,
  Character, ChapterSearchResult, RepetitionWarning,
} from "../types";
import { useReveal } from "../hooks/useReveal";

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
  useReveal(rootRef);

  async function refreshChapters() {
    if (!projectId) return;
    setChapters(await api.listChapters(projectId));
  }

  useEffect(() => {
    if (!projectId) return;
    refreshChapters();
    api.getWorldbuildResult(projectId).then((r) => setCharacters(r.characters));
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
      .catch(() => {
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
    } finally {
      setSaving(false);
    }
  }

  async function handleSearch() {
    if (!projectId || !query.trim()) return;
    setSearching(true);
    try {
      setSearchResults(await api.searchChapters(projectId, query, characterId || undefined));
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
          <label>正文</label>
          <textarea
            rows={8}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="粘贴或输入这一章的正文…保存后会自动标记出场人物、embed 全文并跑一次重复度检测"
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
            还没有章节
            <div className="empty-state__hint">
              从写作控制台点"写 N 章"，写完会自动出现在这里
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

      {/* 单章详情抽屉 */}
      {chapterDetailLoading && (
        <div className="banner banner-info" style={{ marginTop: 16 }}>加载章节详情中…</div>
      )}
      {chapterDetail && !chapterDetailLoading && (
        <div className="card mt-24">
          <div className="flex-between" style={{ marginBottom: 12 }}>
            <h3 className="card__title" style={{ margin: 0 }}>
              第{chapterDetail.chapter_no}章 · {chapterDetail.title || "（无标题）"}
            </h3>
            <button className="btn btn-ghost" onClick={() => setChapterDetail(null)}>关闭</button>
          </div>
          {chapterDetail.characters.length > 0 && (
            <div className="chapter-detail-chars">
              <span className="text-faint" style={{ fontSize: 12 }}>出场人物：</span>
              {chapterDetail.characters.map((cc) => (
                <span key={cc.id} className="legislation-card__chip" style={{ marginRight: 6 }}>
                  {cc.character_name}
                  {cc.character_role ? ` · ${cc.character_role}` : ""}
                </span>
              ))}
            </div>
          )}
          <pre className="log-console" style={{
            marginTop: 12, maxHeight: 480, whiteSpace: "pre-wrap",
            fontFamily: "var(--font-body)", lineHeight: 1.7, fontSize: 13.5,
          }}>{chapterDetail.content}</pre>
        </div>
      )}
    </div>
  );
}
