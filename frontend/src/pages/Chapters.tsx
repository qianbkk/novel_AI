import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ChapterListItem, Character, ChapterSearchResult, RepetitionWarning } from "../types";

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

      <div className="card mt-24">
        <h3 className="card__title">已保存章节</h3>
        {chapters.length === 0 ? (
          <div className="empty-state">
            还没有章节
            <div className="empty-state__hint">
              从写作控制台点"写 N 章"，写完会自动出现在这里
            </div>
          </div>
        ) : (
          chapters.map((c) => (
            <div className="chapter-row" key={c.id}>
              <span className="chapter-row__no">第{c.chapter_no}章</span>
              <div className="chapter-row__title">{c.title || "（无标题）"}</div>
              <div className="chapter-row__preview">{c.content_preview}…</div>
              <div className="chapter-row__meta">{c.word_count.toLocaleString()} 字</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
