/**
 * WorldviewTab — 2026-07-25 抽离（修 P1-1 短板巨型 page 拆解）
 *
 * 世界观 tab 7 段结构化 + 历史时间线 + 卷级骨架。
 * 之前是 WorldBuild.tsx 内嵌私有组件（120 行），拆出来让 WorldBuild.tsx 缩到路由 + 数据层。
 */
import type { WorldBuildResult } from "../../types";

export function WorldviewSection({
  title, text, idx,
}: { title: string; text: string; idx: string }) {
  return (
    <div className="entity-card" style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="mono" style={{ color: "var(--color-accent-strong)", fontSize: 11 }}>
          {idx}
        </span>
        <div className="entity-card__name">{title}</div>
      </div>
      <div className="entity-card__desc">{text}</div>
    </div>
  );
}

export function WorldviewTab({ result }: { result: WorldBuildResult }) {
  // 7 段世界观：worldview_rich 是从 WorldSetting.world_view_rich_json 反序列化字段，
  // 老项目（Phase 1 之前）走 world_view 文本 fallback。
  const rich = (result as any).worldview_rich as null | Record<string, string>;
  const storyCore = (result as any).story_core_struct as null | Record<string, string>;
  const timeline = (result as any).history_timeline as null | Array<{ era: string; event: string; impact: string }>;
  const ws = result.world_setting;

  return (
    <div>
      {/* 故事核心 */}
      <h3 className="module-heading">
        <span className="module-heading__index">M03</span>
        叙事工程 · 故事核心
        <span className="module-heading__sub">主线记忆防线 L1</span>
      </h3>

      {/* 故事核心 4 段 */}
      {storyCore ? (
        <>
          {storyCore.goal && (
            <div className="entity-card">
              <div className="entity-card__name">目标</div>
              <div className="entity-card__desc">{storyCore.goal}</div>
            </div>
          )}
          {storyCore.conflict && (
            <div className="entity-card">
              <div className="entity-card__name">冲突</div>
              <div className="entity-card__desc">{storyCore.conflict}</div>
            </div>
          )}
          {storyCore.theme && (
            <div className="entity-card">
              <div className="entity-card__name">主题</div>
              <div className="entity-card__desc">{storyCore.theme}</div>
            </div>
          )}
          {storyCore.hook && (
            <div className="entity-card">
              <div className="entity-card__name">开篇钩子</div>
              <div className="entity-card__desc">{storyCore.hook}</div>
            </div>
          )}
        </>
      ) : ws?.story_core ? (
        /* 老项目 fallback */
        <div className="entity-card">
          <div className="entity-card__name">故事核心</div>
          <div className="entity-card__desc">{ws.story_core}</div>
        </div>
      ) : null}

      {/* 7 段世界观 */}
      {rich ? (
        <div style={{ marginTop: 18 }}>
          <WorldviewSection title="宇宙观 / 天地法则" text={rich.cosmos} idx="M03.1" />
          <WorldviewSection title="地理总览"           text={rich.geography} idx="M03.2" />
          <WorldviewSection title="历史概述"           text={rich.history} idx="M03.3" />
          <WorldviewSection title="社会制度"           text={rich.society} idx="M03.4" />
          <WorldviewSection title="科技/修炼体系"     text={rich.technology} idx="M03.5" />
          <WorldviewSection title="种族/族群"         text={rich.races} idx="M03.6" />
          <WorldviewSection title="风土人情"           text={rich.customs} idx="M03.7" />
        </div>
      ) : ws?.world_view ? (
        /* 老项目 fallback */
        <div className="entity-card" style={{ marginTop: 18 }}>
          <div className="entity-card__name">世界观设定</div>
          <div className="entity-card__desc">{ws.world_view}</div>
        </div>
      ) : null}

      {/* 老项目 banner 提示升级 */}
      {!rich && !ws?.world_view && (
        <div className="banner banner-warn" style={{ marginTop: 14 }}>
          ⚠ 该项目的世界观尚未升级为结构化数据（老版本 worldbuild）。
          请重新跑 worldbuild 升级为 7 段结构化。
        </div>
      )}

      {/* 历史时间线 */}
      {timeline && timeline.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <h3 className="module-heading">
            <span className="module-heading__index">M03.8</span>
            历史时间线
            <span className="module-heading__sub">{timeline.length} 个大事件</span>
          </h3>
          <div className="history-timeline">
            {timeline.map((node, i) => (
              <div key={i} className="history-timeline__node">
                <span className="history-timeline__era">{node.era}</span>
                <div className="history-timeline__event">{node.event}</div>
                <div className="history-timeline__impact">{node.impact}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 卷级骨架（保留） */}
      {ws?.plot_skeleton_json && ws.plot_skeleton_json.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <h3 className="module-heading">
            <span className="module-heading__index">M03.9</span>
            卷级骨架
            <span className="module-heading__sub">{ws.plot_skeleton_json.length} 卷</span>
          </h3>
          {ws.plot_skeleton_json.map((v, i) => (
            <div className="entity-card" key={i}>
              <div className="entity-card__name">
                <span className="last-chapter-line__no" style={{ marginRight: 8 }}>卷 {i + 1}</span>
                {v.title}
              </div>
              <div className="entity-card__desc">{v.summary}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
