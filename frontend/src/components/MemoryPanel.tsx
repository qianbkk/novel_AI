import type { BridgeMemory } from "../types";

/**
 * 分层记忆快照面板。
 *
 * 存在的理由：在此之前前端的"记忆层"是三个用 `chapters.length` /
 * `log10(字数)` 硬算出来的温度计（还标了本项目根本不存在的 L1/L3 层），
 * 不读任何真实记忆文件 —— 引擎跑飞时它照样一片绿。
 *
 * 现在全部读自 `GET /projects/{id}/bridge/memory`，对应引擎真实落盘的
 * L2（热/冷/约束/meta）+ L5（弧归档）。长篇最致命的三个静默故障
 * —— 伏笔逾期堆积、未过质量门的章节污染后续剧情、tracker 解析失败漏掉
 * 整章状态 —— 在这里第一次成为看得见的信号。
 */

function Stat({ label, value, danger, warn }: {
  label: string; value: React.ReactNode; danger?: boolean; warn?: boolean;
}) {
  // 本项目的危险色是印章红（--color-stamp），没有 --danger 变量
  const color = danger ? "var(--color-stamp)"
    : warn ? "var(--color-warn)"
    : undefined;
  return (
    <div className="memory-stat" data-testid={`memory-stat-${label}`}>
      <span className="memory-stat__num" style={color ? { color } : undefined}>
        {value}
      </span>
      <span className="memory-stat__label">{label}</span>
    </div>
  );
}

function Chips({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) return <span className="text-faint" style={{ fontSize: 12 }}>{empty}</span>;
  return (
    <div className="memory-chips">
      {items.map((t, i) => <span className="memory-chip" key={i}>{t}</span>)}
    </div>
  );
}

export function MemoryPanel({ memory, loading, error, onRefresh }: {
  memory: BridgeMemory | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const header = (
    <div className="memory-panel__head">
      <h4 className="memory-panel__title">分层记忆快照</h4>
      <button
        className="btn btn-ghost btn-sm"
        onClick={onRefresh}
        disabled={loading}
        data-testid="memory-refresh"
      >
        {loading ? "读取中…" : "刷新"}
      </button>
    </div>
  );

  if (error) {
    return (
      <div className="card memory-panel" data-testid="memory-panel">
        {header}
        <div className="banner banner-danger" data-testid="memory-error">
          记忆快照读取失败：{error}
        </div>
      </div>
    );
  }

  if (!memory || !memory.available) {
    return (
      <div className="card memory-panel" data-testid="memory-panel">
        {header}
        <div className="banner banner-info" data-testid="memory-empty">
          {memory?.message || "记忆文件尚未生成 —— 先跑一次写作命令，tracker 会在每章结束后写入。"}
        </div>
      </div>
    );
  }

  const s = memory.stats;
  const hot = memory.l2.hot || {};
  const cold = memory.l2.cold || {};
  const constraints = memory.l2.constraints || {};
  const summaries = hot.recent_summaries || [];
  const foreshadow = constraints.foreshadowing_planted || [];
  const overdue = foreshadow.filter((f) => f.overdue);
  const truncated = s.recent_summaries_total > summaries.length;

  return (
    <div className="card memory-panel" data-testid="memory-panel">
      {header}

      {/* ─── 汇总指标：红色的都是长篇会翻车的信号 ─── */}
      <div className="memory-stats" data-testid="memory-stats">
        <Stat label="已追踪章" value={s.total_chapters_tracked} />
        <Stat label="记忆止于第" value={`${s.last_updated_chapter} 章`} />
        <Stat label="活跃剧情线" value={s.active_thread_count} />
        <Stat label="角色状态" value={s.character_state_count} />
        <Stat label="生效约束" value={s.constraint_count} />
        <Stat label="既定事实" value={s.established_fact_count} />
        <Stat
          label="伏笔未回收"
          value={`${s.foreshadowing_planted_count - s.foreshadowing_resolved_count}`}
          warn={s.foreshadowing_planted_count - s.foreshadowing_resolved_count > 10}
        />
        <Stat label="伏笔逾期" value={s.foreshadowing_overdue_count}
              danger={s.foreshadowing_overdue_count > 0} />
        <Stat label="待修订章" value={s.unverified_chapter_count}
              danger={s.unverified_chapter_count > 0} />
        <Stat label="tracker 解析失败" value={s.tracker_parse_failure_count}
              danger={s.tracker_parse_failure_count > 0} />
        <Stat label="弧归档" value={memory.l5_available ? s.arc_count : "—"} />
        <Stat label="弧长基准"
              value={s.chapters_per_arc ? `${s.chapters_per_arc} 章` : "未定"} />
      </div>

      {/* ─── 告警：静默故障必须显式说出来 ─── */}
      {s.foreshadowing_overdue_count > 0 && (
        <div className="banner banner-danger" data-testid="memory-alert-overdue">
          {s.foreshadowing_overdue_count} 条伏笔已过应回收章节。长篇里伏笔堆积不回收
          是读者弃书的头号原因，建议在后续章节大纲里安排回收。
        </div>
      )}
      {s.unverified_chapter_count > 0 && (
        <div className="banner banner-warn" data-testid="memory-alert-unverified">
          第 {s.unverified_chapters.join("、")} 章未通过质量门，已按草稿计入记忆
          （摘要标记为「待修订」，不会被当成既成事实）。建议人工修订后重跑。
        </div>
      )}
      {s.tracker_parse_failure_count > 0 && (
        <div className="banner banner-danger" data-testid="memory-alert-tracker">
          tracker 有 {s.tracker_parse_failure_count} 次解析失败
          {memory.l2.meta?.last_tracker_parse_failure_chapter
            ? `（最近一次在第 ${memory.l2.meta.last_tracker_parse_failure_chapter} 章）`
            : ""}
          —— 这些章的状态变化没有进记忆，会造成后续设定漂移。
        </div>
      )}

      {/* ─── L2 热层 ─── */}
      <div className="memory-layer" data-testid="memory-l2-hot">
        <div className="memory-layer__head">
          <span className="memory-row__layer">L2 热</span>
          <span className="memory-layer__desc">当前状态 · 每章刷新</span>
        </div>
        <dl className="memory-kv">
          <dt>主角境界</dt>
          <dd>{hot.protagonist_level || "—"}
            {hot.protagonist_level_num != null && ` (Lv${hot.protagonist_level_num})`}</dd>
          <dt>点数</dt><dd>{hot.protagonist_points ?? "—"}</dd>
          <dt>所在地</dt><dd>{hot.scene_location || "—"}</dd>
          <dt>时间</dt><dd>{hot.time_context || "—"}</dd>
        </dl>
        <div className="memory-sub">
          <div className="memory-sub__label">道具</div>
          <Chips items={hot.inventory || []} empty="无" />
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">活跃剧情线</div>
          <Chips items={hot.active_threads || []} empty="无（写了几章还是空，多半是 tracker 没提取到）" />
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">角色状态</div>
          {Object.keys(hot.character_states || {}).length === 0 ? (
            <span className="text-faint" style={{ fontSize: 12 }}>无</span>
          ) : (
            <ul className="memory-list">
              {Object.entries(hot.character_states || {}).map(([name, st]) => (
                <li key={name}><strong>{name}</strong>：{st}</li>
              ))}
            </ul>
          )}
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">
            近期章节摘要
            {truncated && (
              <span className="text-faint" style={{ fontWeight: 400, marginLeft: 6 }}>
                （共 {s.recent_summaries_total} 章，只显示最近 {summaries.length} 章）
              </span>
            )}
          </div>
          {summaries.length === 0 ? (
            <span className="text-faint" style={{ fontSize: 12 }}>无</span>
          ) : (
            <ul className="memory-list" data-testid="memory-summaries">
              {summaries.map((it, i) => (
                <li key={i} className={it.unverified ? "is-unverified" : undefined}>
                  <strong>第 {it.chapter ?? "?"} 章</strong>：{it.summary}
                  {it.unverified && <span className="memory-tag-warn">待修订</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
        {hot.last_chapter_ending && (
          <div className="memory-sub">
            <div className="memory-sub__label">上一章结尾</div>
            <p className="memory-quote">{hot.last_chapter_ending}</p>
          </div>
        )}
      </div>

      {/* ─── L2 约束层：伏笔是这里最重要的东西 ─── */}
      <div className="memory-layer" data-testid="memory-l2-constraints">
        <div className="memory-layer__head">
          <span className="memory-row__layer">L2 约</span>
          <span className="memory-layer__desc">伏笔 · 禁止项 · 既定事实</span>
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">
            伏笔（{foreshadow.length} 条已埋，{overdue.length} 条逾期）
          </div>
          {foreshadow.length === 0 ? (
            <span className="text-faint" style={{ fontSize: 12 }}>无</span>
          ) : (
            <ul className="memory-list" data-testid="memory-foreshadowing">
              {/* 逾期的排最前 —— 面板是给人做决定用的，不是流水账 */}
              {[...foreshadow].sort((a, b) => a.due_chapter - b.due_chapter).map((f, i) => (
                <li key={i} className={f.overdue ? "is-overdue" : undefined}>
                  {f.desc || "（无描述）"}
                  <span className="text-faint" style={{ marginLeft: 6, fontSize: 11.5 }}>
                    第 {f.planted_at_chapter ?? "?"} 章埋 → 应第 {f.due_chapter} 章回收
                  </span>
                  {f.overdue && <span className="memory-tag-danger">逾期</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">生效中的禁止项</div>
          {(constraints.forbidden_constraints || []).length === 0 ? (
            <span className="text-faint" style={{ fontSize: 12 }}>无</span>
          ) : (
            <ul className="memory-list">
              {(constraints.forbidden_constraints || []).map((c, i) => (
                <li key={i}>
                  {String(c.desc ?? "")}
                  <span className="text-faint" style={{ marginLeft: 6, fontSize: 11.5 }}>
                    第 {String(c.expires_at_chapter ?? "?")} 章失效
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">既定事实（不可推翻）</div>
          {(constraints.established_facts || []).length === 0 ? (
            <span className="text-faint" style={{ fontSize: 12 }}>无</span>
          ) : (
            <ul className="memory-list">
              {(constraints.established_facts || []).map((f, i) => (
                <li key={i}>
                  {String(f.fact ?? "")}
                  <span className="text-faint" style={{ marginLeft: 6, fontSize: 11.5 }}>
                    第 {String(f.established_at_chapter ?? "?")} 章确立
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* ─── L2 冷层 ─── */}
      <div className="memory-layer" data-testid="memory-l2-cold">
        <div className="memory-layer__head">
          <span className="memory-row__layer">L2 冷</span>
          <span className="memory-layer__desc">
            世界事件 · 已闭合线 · 已回收伏笔
            {s.world_events_total > (cold.world_events || []).length &&
              `（世界事件共 ${s.world_events_total} 条，只显示最近 ${(cold.world_events || []).length} 条）`}
          </span>
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">世界事件</div>
          <Chips items={cold.world_events || []} empty="无" />
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">已闭合剧情线</div>
          <Chips items={cold.closed_threads || []} empty="无" />
        </div>
        <div className="memory-sub">
          <div className="memory-sub__label">已回收伏笔</div>
          <Chips items={cold.resolved_foreshadowing || []} empty="无" />
        </div>
      </div>

      {/* ─── L5 弧归档 ─── */}
      <div className="memory-layer" data-testid="memory-l5">
        <div className="memory-layer__head">
          <span className="memory-row__layer">L5</span>
          <span className="memory-layer__desc">弧归档 · 跨百章的长程记忆</span>
        </div>
        {!memory.l5_available ? (
          <span className="text-faint" style={{ fontSize: 12 }}>
            尚无弧归档（要跑完至少一个完整故事弧才会生成）
          </span>
        ) : (
          <>
            <div className="memory-sub">
              <div className="memory-sub__label">弧总结</div>
              {(memory.l5.arc_summaries || []).length === 0 ? (
                <span className="text-faint" style={{ fontSize: 12 }}>无</span>
              ) : (
                <ul className="memory-list">
                  {(memory.l5.arc_summaries || []).map((a, i) => (
                    <li key={i}>
                      <strong>弧 {String(a.arc ?? i + 1)}</strong>：{String(a.summary ?? "")}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="memory-sub">
              <div className="memory-sub__label">角色成长线</div>
              {Object.keys(memory.l5.character_arcs || {}).length === 0 ? (
                <span className="text-faint" style={{ fontSize: 12 }}>无</span>
              ) : (
                <ul className="memory-list">
                  {Object.entries(memory.l5.character_arcs || {}).map(([n, a]) => (
                    <li key={n}><strong>{n}</strong>：{a}</li>
                  ))}
                </ul>
              )}
            </div>
            <div className="memory-sub">
              <div className="memory-sub__label">重大揭示</div>
              <Chips items={memory.l5.major_revelations || []} empty="无" />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default MemoryPanel;
