import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  ChapterListItem, ChapterFull, ChapterCharacter,
  Character, ChapterSearchResult, RepetitionWarning,
} from "../types";
import { useReveal } from "../hooks/useReveal";
import { useToast } from "../components/Toast";

// 衔接锁四个字段：场景布置 / 角色登场 / 物品状态 / 前章收尾
function deriveSceneLayout(text: string): string {
  return text.split(/[。！？!?]/)[0]?.slice(0, 36) || "—";
}

function deriveItemState(text: string): string {
  const itemKeywords = ["灵石", "剑", "玉佩", "丹药", "符", "丹", "阵", "卷轴", "宝", "法器", "丹炉"];
  const matched = itemKeywords.filter((k) => text.includes(k));
  return matched.length > 0 ? matched.join("、") : "—";
}

// 修订 2026-07-16：判定章节状态用于筛选 / 状态指示器
type ChapterStatus = "ok" | "incomplete" | "escalate";
function deriveStatus(c: ChapterListItem): ChapterStatus {
  const preview = c.content_preview || "";
  if (preview.startsWith("[待修订]") || (c.title || "").includes("[待修订]")) return "escalate";
  if (!c.title || /^第\d+章$/.test(c.title)) return "incomplete";
  return "ok";
}

export default function Chapters() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
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

  // 跳到指定章节号（输入 chapter_no 一键查看全文）
  const [jumpChapterNo, setJumpChapterNo] = useState("");

  // 单章详情 → 改为独立 ChapterReader 页（见 App.tsx 路由），
// 不再用 Dialog 弹窗模式（2026-07-16 Issue #11）

  const [expandedLock, setExpandedLock] = useState<string | null>(null);
  const [charactersByChapter, setCharactersByChapter] = useState<Record<string, ChapterCharacter[]>>({});

  // 修订 2026-07-16：章节状态筛选（全部/完整/待修订/无标题）
  const [statusFilter, setStatusFilter] = useState<"all" | ChapterStatus>("all");

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

  // 打开单章详情 — 修订 2026-07-16：跳到独立 ChapterReader 页（替代 Dialog）
  function openChapterDetail(chapterNo: number) {
    if (!projectId) return;
    navigate(`/projects/${projectId}/chapter/${chapterNo}`);
  }

  // 重新导入章节：从 output/chapters 重新派生 title/summary（用于修旧标题）
  // 修订 2026-07-16：用户报告「300 章标题还是错的」 — 加这个按钮让用户手动触发
  const [reimporting, setReimporting] = useState(false);
  async function handleReimport() {
    if (!projectId) return;
    if (!confirm("重新导入所有章节的标题？从输出目录重新派生（基于内容首句），不会动章节正文。")) return;
    setReimporting(true);
    try {
      const updated = await api.reimportChapters(projectId);
      toast.success("重新导入完成", `${updated.length} 章标题已更新`);
      await refreshChapters();
    } catch (e) {
      toast.error("重新导入失败", String(e));
    } finally {
      setReimporting(false);
    }
  }

  // 修订 2026-07-16（Issue #12）：调 LLM 真正生成章节标题
  // 区别于 handleReimport：那个从内容首句机械截，这个走 LLM 读章节内容生成标题。
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiResult, setAiResult] = useState<{
    processed: number;
    updated: number;
    cost: number;
    sample: { chapter_no: number; old_title: string | null; new_title: string }[];
  } | null>(null);
  async function handleAiGenerateTitles(mode: "all" | "missing" | "sample5") {
    if (!projectId) return;
    const onlyMissing = mode !== "all";
    const limit = mode === "sample5" ? 5 : undefined;
    const sample = mode === "sample5";
    if (mode === "all" && !confirm("调 LLM 为所有章节生成标题？大约每章 ~$0.002，300 章约 $0.6。")) return;
    setAiGenerating(true);
    setAiResult(null);
    try {
      const res = await api.regenerateTitles(projectId, {
        limit, only_missing: onlyMissing, sample,
      });
      setAiResult({
        processed: res.processed,
        updated: res.updated,
        cost: res.total_cost_usd,
        sample: res.changes.slice(0, 5),
      });
      if (sample) {
        toast.info("样例生成完成", `样本 5 章 / 成本 $${res.total_cost_usd.toFixed(4)}`);
      } else {
        toast.success("AI 标题生成完成", `${res.updated} 章已更新 / 成本 $${res.total_cost_usd.toFixed(4)}`);
        await refreshChapters();
      }
    } catch (e) {
      toast.error("生成失败", String(e));
    } finally {
      setAiGenerating(false);
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

  // 修订 2026-07-16：状态分组 + 筛选
  const statusCounts = useMemo(() => {
    const counts = { all: chapters.length, ok: 0, incomplete: 0, escalate: 0 };
    for (const c of chapters) {
      counts[deriveStatus(c)]++;
    }
    return counts;
  }, [chapters]);

  const filteredChapters = useMemo(() => {
    if (statusFilter === "all") return chaptersDesc;
    return chaptersDesc.filter((c) => deriveStatus(c) === statusFilter);
  }, [chaptersDesc, statusFilter]);

  // 修订 2026-07-16：第一章 / 末章快跳（修订：跳独立 ChapterReader 页）
  const firstChapter = chapters.length > 0 ? [...chapters].sort((a, b) => a.chapter_no - b.chapter_no)[0] : null;
  const lastChapter = chapters.length > 0 ? chaptersDesc[0] : null;

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
        {chapters.length > 0 && (
          <div className="page-header__actions">
            <button
              className="btn btn-ghost"
              onClick={handleReimport}
              disabled={reimporting || aiGenerating}
              title="从输出目录重新派生所有章节标题（基于内容首句）"
            >
              {reimporting ? "重新导入中…" : "🔄 重新派生（首句）"}
            </button>
            <button
              className="btn btn-primary"
              onClick={() => handleAiGenerateTitles("sample5")}
              disabled={aiGenerating || reimporting}
              title="调 LLM 读章节内容生成真正像样的标题（先看 5 章样例）"
              style={{ marginLeft: 6 }}
            >
              {aiGenerating ? "生成中…" : "✨ AI 生成标题"}
            </button>
          </div>
        )}
      </div>

      {/* AI 生成结果面板 */}
      {aiResult && (
        <div className="card mt-16" style={{ borderColor: "var(--color-accent)" }}>
          <div className="flex-between" style={{ marginBottom: 8 }}>
            <h3 className="card__title" style={{ margin: 0 }}>
              ✨ AI 标题生成结果
            </h3>
            <button className="btn btn-ghost" onClick={() => setAiResult(null)}>×</button>
          </div>
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
            处理 {aiResult.processed} 章 · 更新 {aiResult.updated} 章 · 成本 ${aiResult.cost.toFixed(4)}
          </div>
          {aiResult.sample.length > 0 && (
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 60 }}>章</th>
                  <th>旧标题</th>
                  <th>新标题（LLM 生成）</th>
                </tr>
              </thead>
              <tbody>
                {aiResult.sample.map((s) => (
                  <tr key={s.chapter_no}>
                    <td className="mono">Ch{s.chapter_no}</td>
                    <td className="text-faint" style={{ fontSize: 12 }}>
                      {s.old_title || "（无）"}
                    </td>
                    <td style={{ fontFamily: "var(--font-display)", fontWeight: 600, color: "var(--color-accent)" }}>
                      第{s.chapter_no}章·{s.new_title}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!aiResult.sample.length && (
            <div className="empty-state">没有章节需要更新标题</div>
          )}
          <div className="button-row" style={{ marginTop: 12 }}>
            <button
              className="btn"
              onClick={() => handleAiGenerateTitles("missing")}
              disabled={aiGenerating}
              title="为所有缺标题的章节生成"
            >
              生成全部缺失
            </button>
            <button
              className="btn btn-primary"
              onClick={() => handleAiGenerateTitles("all")}
              disabled={aiGenerating}
              title="覆盖所有章节标题（包括已有的）"
            >
              强制重新生成全部
            </button>
          </div>
        </div>
      )}

      {/* ============ 阅读导航（最显眼位置） ============ */}
      <div className="card reading-navigator">
        <h3 className="card__title">📖 阅读导航</h3>
        <div className="text-muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 14 }}>
          点按钮直接打开章节全文 Dialog。章节正文在主区域列表里点章节号也能进。
        </div>
        <div className="reading-navigator__row">
          <button
            className="btn btn-ghost"
            disabled={!firstChapter}
            onClick={() => firstChapter && openChapterDetail(firstChapter.chapter_no)}
          >
            ⏮ 第一章
          </button>
          <div className="reading-navigator__jump">
            <input
              type="number"
              min={1}
              placeholder="如 42"
              value={jumpChapterNo}
              onChange={(e) => setJumpChapterNo(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && jumpChapterNo.trim()) {
                  const target = chapters.find((c) => c.chapter_no === Number(jumpChapterNo));
                  if (target) openChapterDetail(target.chapter_no);
                  else toast.warn("找不到该章节号", `已保存 ${chapters[0]?.chapter_no ?? "-"} - ${chapters[chapters.length - 1]?.chapter_no ?? "-"}`);
                }
              }}
            />
            <button
              className="btn btn-primary"
              disabled={!jumpChapterNo.trim() || !chapters.length}
              onClick={() => {
                const target = chapters.find((c) => c.chapter_no === Number(jumpChapterNo));
                if (target) openChapterDetail(target.chapter_no);
                else toast.warn("找不到该章节号");
              }}
            >
              阅读第 N 章
            </button>
          </div>
          <button
            className="btn btn-ghost"
            disabled={!lastChapter}
            onClick={() => lastChapter && openChapterDetail(lastChapter.chapter_no)}
          >
            最新章 ⏭
          </button>
        </div>
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
        <h3 className="card__title">已保存章节 · {chapters.length} 章</h3>

        {/* 修订 2026-07-16：状态筛选 chips */}
        <div className="chapter-filter">
          <button
            className={`chapter-filter__chip ${statusFilter === "all" ? "is-active" : ""}`}
            onClick={() => setStatusFilter("all")}
          >
            全部 <span className="chapter-filter__count">{statusCounts.all}</span>
          </button>
          <button
            className={`chapter-filter__chip chapter-filter__chip--ok ${statusFilter === "ok" ? "is-active" : ""}`}
            onClick={() => setStatusFilter("ok")}
          >
            ✓ 完整 <span className="chapter-filter__count">{statusCounts.ok}</span>
          </button>
          <button
            className={`chapter-filter__chip chapter-filter__chip--warn ${statusFilter === "incomplete" ? "is-active" : ""}`}
            onClick={() => setStatusFilter("incomplete")}
          >
            ⚠ 无标题 <span className="chapter-filter__count">{statusCounts.incomplete}</span>
          </button>
          <button
            className={`chapter-filter__chip chapter-filter__chip--escalate ${statusFilter === "escalate" ? "is-active" : ""}`}
            onClick={() => setStatusFilter("escalate")}
          >
            🚨 待修订 <span className="chapter-filter__count">{statusCounts.escalate}</span>
          </button>
        </div>

        <div className="text-muted" style={{ fontSize: 11.5, marginTop: 8, marginBottom: 12 }}>
          点击章节号 / 标题直接打开章节全文 Dialog（ESC 关闭）；点击 ▸ 展开章节衔接锁。
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
        ) : filteredChapters.length === 0 ? (
          <div className="empty-state">当前筛选下没有章节。</div>
        ) : (
          filteredChapters.map((c) => {
            const prev = chapters.find((x) => x.chapter_no === c.chapter_no - 1);
            const text = c.content_preview || "";
            const sceneLayout = deriveSceneLayout(text);
            const itemState = deriveItemState(text);
            const prevCliff = prev ? (prev.content_preview || "").slice(-30) : "—";
            const hasPrevChapter = c.chapter_no > 1;
            const isOpen = expandedLock === c.id;
            const realChars = charactersByChapter[c.id];
            const characterNames = realChars?.map((cc) => cc.character_name).filter(Boolean) as string[] | undefined;
            const status = deriveStatus(c);
            const statusBadge =
              status === "ok" ? <span className="chapter-row__status chapter-row__status--ok">✓</span> :
              status === "incomplete" ? <span className="chapter-row__status chapter-row__status--warn">⚠</span> :
              <span className="chapter-row__status chapter-row__status--escalate">🚨</span>;
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
                <summary
                  className={`chapter-row chapter-row--clickable ${isOpen ? "is-locked" : ""}`}
                  onClick={(e) => {
                    // 修订 2026-07-16：整行可点击打开 Dialog（除了 ▸ 和右侧按钮）
                    const tag = (e.target as HTMLElement).tagName;
                    if (tag === "DETAILS" || tag === "SUMMARY") {
                      // SUMMARY 的默认行为是切换 details，让 details toggle 自然发生
                      return;
                    }
                  }}
                >
                  <span className="chapter-row__chevron" aria-hidden="true">▸</span>
                  {statusBadge}
                  <button
                    className="chapter-row__no chapter-row__no--btn"
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); openChapterDetail(c.chapter_no); }}
                    aria-label={`阅读第${c.chapter_no}章`}
                  >
                    第{c.chapter_no}章
                  </button>
                  <button
                    className="chapter-row__title chapter-row__title--btn"
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); openChapterDetail(c.chapter_no); }}
                  >
                    {c.title || "（无标题 — 点击阅读）"}
                  </button>
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

      {/* ============ 高级操作（折叠）：手动录入 + 语义检索 ============ */}
      <details className="card mt-24 advanced-section">
        <summary className="advanced-section__summary">
          <span>▼ 高级操作：手动新增 / 语义检索</span>
          <span className="text-faint" style={{ fontSize: 12 }}>手动录入章节、跨章检索</span>
        </summary>
        <div className="advanced-section__body">
          <div className="card__subsection">
            <h4 className="card__title" style={{ fontSize: 14 }}>手动新增一章</h4>
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
                rows={8}
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
                <a onClick={() => openChapterDetail((chapters.find(c => c.id === lastSavedChapterId)?.chapter_no) ?? 1)}
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

          <div className="card__subsection">
            <h4 className="card__title" style={{ fontSize: 14 }}>语义检索</h4>
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
        </div>
      </details>


    </div>
  );
}
