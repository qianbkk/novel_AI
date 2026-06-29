import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ChapterListItem, Character, ChapterSearchResult, RepetitionWarning } from "../types";

// 从章节 preview 中启发式推断衔接锁四个字段
function deriveConnectionLock(c: ChapterListItem, all: ChapterListItem[]) {
  const prev = [...all].reverse().find((x) => x.chapter_no === c.chapter_no - 1);
  const text = c.content_preview || "";

  // 场景布置：取 preview 第一句（到第一个句号或 30 字内）
  const sceneLayout = text.split(/[。！？!?]/)[0]?.slice(0, 36) || "—";

  // 角色登场：从 preview 里过一遍 role 名，对上就当作登场
  const appearance: string[] = [];

  // 物品状态：粗略匹配 "灵石"、"剑"、"丹" 等常见物件关键词
  const itemKeywords = ["灵石", "剑", "玉佩", "丹药", "符", "丹", "阵", "卷轴", "宝", "法器", "丹炉"];
  const matched = itemKeywords.filter((k) => text.includes(k));

  // 前章收尾：上一章 preview 的最后一句
  const prevCliff = prev ? (prev.content_preview || "").slice(-30) : "—";

  return {
    sceneLayout,
    appearance,
    itemState: matched.length > 0 ? matched.join("、") : "—",
    prevCliff,
  };
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

  const [query, setQuery] = useState("");
  const [characterId, setCharacterId] = useState("");
  const [searchResults, setSearchResults] = useState<ChapterSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);

  // 衔接锁下拉：只展开一个
  const [expandedLock, setExpandedLock] = useState<string | null>(null);

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

  async function handleSave() {
    if (!projectId || !content.trim()) return;
    setSaving(true);
    setRepetitionWarnings([]);
    try {
      const res = await api.createChapter(projectId, { chapter_no: chapterNo, title, content });
      setRepetitionWarnings(res.repetition_warnings);
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

  // 倒序排列用于显示（最新在前）
  const chaptersDesc = useMemo(() => [...chapters].sort((a, b) => b.chapter_no - a.chapter_no), [chapters]);

  return (
    <div>
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
      <div className="card mt-24">
        <h3 className="card__title">已保存章节 · 衔接锁</h3>
        <div className="text-muted" style={{ fontSize: 11.5, marginTop: -10, marginBottom: 12 }}>
          点击章节号可展开"章节衔接锁"：场景布置 / 角色登场 / 物品状态 / 前章收尾
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
            const lock = deriveConnectionLock(c, chapters);
            const isOpen = expandedLock === c.id;
            const hasPrevChapter = c.chapter_no > 1;
            return (
              <div key={c.id} style={{ position: "relative" }}>
                <div
                  className={`chapter-row ${isOpen ? "is-locked" : ""}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => setExpandedLock(isOpen ? null : c.id)}
                >
                  <span className="chapter-row__no">{isOpen ? "▼" : "▶"} 第{c.chapter_no}章</span>
                  <div className="chapter-row__title">{c.title || "（无标题）"}</div>
                  <div className="chapter-row__preview">{c.content_preview}…</div>
                  <div className="chapter-row__meta">{c.word_count.toLocaleString()} 字</div>
                </div>
                <span className="lock-mark" />
                {isOpen && (
                  <div className="conn-lock" style={{ margin: "0 0 14px 56px" }}>
                    <div className="conn-lock__pane">
                      <div className="conn-lock__head">场景布置</div>
                      <ul className="conn-lock__list">
                        <li className="conn-lock__item">{lock.sceneLayout}</li>
                      </ul>
                    </div>
                    <div className="conn-lock__pane">
                      <div className="conn-lock__head">角色登场 / 离场</div>
                      <ul className="conn-lock__list">
                        {lock.appearance.length > 0 ? (
                          lock.appearance.map((n) => (
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
                        <li className="conn-lock__item">{lock.itemState}</li>
                      </ul>
                    </div>
                    <div className="conn-lock__pane">
                      <div className="conn-lock__head">前章收尾 / 后章悬念</div>
                      <ul className="conn-lock__list">
                        <li className={`conn-lock__item ${!hasPrevChapter ? "conn-lock__item--empty" : ""}`}>
                          {hasPrevChapter ? lock.prevCliff : "（本书第一章，无前章）"}
                        </li>
                      </ul>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
