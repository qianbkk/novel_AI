/**
 * 角色卡详情页 — /projects/{pid}/characters/{cid}
 *
 * 8 段结构化展示：
 *   1. 基础信息  2. 外貌描写  3. 性格特征  4. 背景故事
 *   5. 能力体系  6. 口癖台词  7. 持有道具  8. 成长弧
 *
 * 老项目（card=null）显示「该角色暂无详情」友好提示，不爆。
 * 复用 styles.css 已有类：card / entity-card / module-heading /
 * legislation-card / tier-rail / chip / empty-state。
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { CharacterCard, CharacterCardOut, CharacterRelation } from "../types";

export default function CharacterCardPage() {
  const { projectId, characterId } = useParams();
  const navigate = useNavigate();

  const [data, setData] = useState<CharacterCardOut | null>(null);
  const [relations, setRelations] = useState<CharacterRelation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function refresh() {
    if (!projectId || !characterId) return;
    setLoading(true);
    setError(null);
    try {
      const [card, rels] = await Promise.all([
        api.getCharacterCard(projectId, characterId),
        api.getCharacterRelations(projectId, characterId),
      ]);
      if (!mountedRef.current) return;
      setData(card);
      setRelations(rels);
    } catch (e) {
      if (!mountedRef.current) return;
      setError(String(e));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // 依赖项已隐含 projectId/characterId 变化触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, characterId]);

  return (
    <div>
      <div className="page-header">
        <div>
          <button
            className="btn btn-ghost"
            onClick={() => navigate(`/projects/${projectId}/worldbuild`)}
            style={{ marginBottom: 6, padding: "4px 10px", fontSize: 12 }}
          >
            ← 返回世界构建
          </button>
          <h1 className="page-header__title">{data?.name ?? "加载中…"}</h1>
          <div className="page-header__sub">
            {data?.role || ""}
            {data?.faction ? ` · ${data.faction.name}` : ""}
          </div>
        </div>
      </div>

      {error && (
        <div className="banner banner-danger" role="alert">
          <span>{error}</span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => refresh()}
            disabled={loading}
          >
            重试
          </button>
        </div>
      )}

      {loading && <p className="loading-text">加载中…</p>}

      {!loading && !data?.card && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state__title">该角色暂无详情</div>
            <div className="empty-state__hint">
              老版本 worldbuild 没有生成结构化角色卡。
              请到世界构建页重新跑一次以升级数据。
            </div>
            <div className="empty-state__action">
              <button
                className="btn btn-primary"
                onClick={() => navigate(`/projects/${projectId}/worldbuild`)}
              >
                去世界构建页 →
              </button>
            </div>
          </div>
        </div>
      )}

      {!loading && data?.card && (
        <CharacterCardSections card={data.card} relations={relations} />
      )}
    </div>
  );
}

function CharacterCardSections({
  card,
  relations,
}: {
  card: CharacterCard;
  relations: CharacterRelation[];
}) {
  return (
    <>
      {/* 1. 基础信息 */}
      <SectionCard title="基础信息" idx="M04.1">
        <div className="legislation-card__chips">
          {card.basic?.gender && (
            <span className="legislation-card__chip">性别 · {card.basic.gender}</span>
          )}
          {card.basic?.age !== undefined && card.basic?.age !== null && (
            <span className="legislation-card__chip">年龄 · {card.basic.age}</span>
          )}
          {card.basic?.identity && (
            <span className="legislation-card__chip">身份 · {card.basic.identity}</span>
          )}
        </div>
      </SectionCard>

      {/* 2. 外貌描写 */}
      {card.appearance && (
        <SectionCard title="外貌描写" idx="M04.2">
          <p className="entity-card__desc">
            {[
              card.appearance.height && `身高 ${card.appearance.height}`,
              card.appearance.hair && `发色 ${card.appearance.hair}`,
              card.appearance.outfit && `服装 ${card.appearance.outfit}`,
              card.appearance.distinguishing_feature && `辨识特征 ${card.appearance.distinguishing_feature}`,
            ].filter(Boolean).join("；")}
          </p>
        </SectionCard>
      )}

      {/* 3. 性格特征 */}
      {card.personality && (
        <SectionCard title="性格特征" idx="M04.3">
          <div className="legislation-card__chips" style={{ marginBottom: 8 }}>
            {card.personality.tags.map((tag, i) => (
              <span key={i} className="legislation-card__chip">{tag}</span>
            ))}
          </div>
          <p className="entity-card__desc">{card.personality.summary}</p>
        </SectionCard>
      )}

      {/* 4. 背景故事 */}
      {card.background && (
        <SectionCard title="背景故事" idx="M04.4">
          {card.background.origin && (
            <div className="entity-card" style={{ marginBottom: 8 }}>
              <div className="entity-card__name">出身</div>
              <div className="entity-card__desc">{card.background.origin}</div>
            </div>
          )}
          {card.background.motivation && (
            <div className="entity-card" style={{ marginBottom: 8 }}>
              <div className="entity-card__name">动机</div>
              <div className="entity-card__desc">{card.background.motivation}</div>
            </div>
          )}
          {card.background.secret && (
            <div className="entity-card">
              <div className="entity-card__name">隐藏秘密</div>
              <div className="entity-card__desc">{card.background.secret}</div>
            </div>
          )}
        </SectionCard>
      )}

      {/* 5. 能力体系 */}
      {card.abilities && (
        <SectionCard title="能力体系" idx="M04.5">
          <div className="entity-card" style={{ marginBottom: 8 }}>
            <div className="entity-card__name">
              {card.abilities.power_name}
            </div>
            <div className="entity-card__meta" style={{ marginTop: 4 }}>
              当前：{card.abilities.current_tier} · 成长上限：{card.abilities.growth_potential}
            </div>
          </div>
        </SectionCard>
      )}

      {/* 6. 口癖台词 */}
      {card.catchphrase && card.catchphrase.lines.length > 0 && (
        <SectionCard title="口癖台词" idx="M04.6">
          {card.catchphrase.lines.map((line, i) => (
            <blockquote
              key={i}
              style={{
                margin: "6px 0",
                padding: "8px 14px",
                borderLeft: "3px solid var(--color-accent-strong)",
                background: "var(--color-bg-1)",
                fontStyle: "italic",
                color: "var(--color-fg-2)",
                fontSize: 14,
              }}
            >
              "{line}"
            </blockquote>
          ))}
        </SectionCard>
      )}

      {/* 7. 持有道具 */}
      {card.props && (
        <SectionCard title="持有道具" idx="M04.7">
          <div className="legislation-card__chips">
            {card.props.signature_item && (
              <span className="legislation-card__chip">信物 · {card.props.signature_item}</span>
            )}
            {card.props.companion && (
              <span className="legislation-card__chip">同行 · {card.props.companion}</span>
            )}
          </div>
        </SectionCard>
      )}

      {/* 8. 成长弧 */}
      <SectionCard title="成长弧" idx="M04.8">
        <div className="entity-card" style={{ marginBottom: 8 }}>
          <div className="entity-card__name">起点状态</div>
          <div className="entity-card__desc">{card.arc.start_state}</div>
        </div>
        <div className="entity-card" style={{ marginBottom: 8 }}>
          <div className="entity-card__name">转折事件</div>
          <div className="entity-card__desc">{card.arc.catalyst}</div>
        </div>
        <div className="entity-card">
          <div className="entity-card__name">终点状态</div>
          <div className="entity-card__desc">{card.arc.end_state}</div>
        </div>
      </SectionCard>

      {/* 关系边（如果有） */}
      {relations.length > 0 && (
        <SectionCard title="人物关系" idx="M04.9" sub={`${relations.length} 条关系边`}>
          {relations.map((r) => (
            <div key={r.id} className="entity-card" style={{ marginBottom: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span className="entity-card__name">
                  {r.target.name} · {r.relation}
                </span>
                {r.mutual && <span className="badge badge-soft">双向</span>}
                {r.intensity !== null && r.intensity !== undefined && (
                  <span className="badge badge-soft">强度 {r.intensity}/10</span>
                )}
              </div>
              {r.description && (
                <div className="entity-card__desc" style={{ marginTop: 4 }}>
                  {r.description}
                </div>
              )}
              {r.evolution && r.evolution.length > 0 && (
                <div style={{ marginTop: 8, fontSize: 12, color: "var(--color-fg-3)" }}>
                  {r.evolution.map((e, i) => (
                    <div key={i}>· <strong>{e.phase}</strong>：{e.state}</div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </SectionCard>
      )}
    </>
  );
}

function SectionCard({
  title,
  idx,
  sub,
  children,
}: {
  title: string;
  idx: string;
  sub?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <h3 className="module-heading">
        <span className="module-heading__index">{idx}</span>
        {title}
        {sub && <span className="module-heading__sub">{sub}</span>}
      </h3>
      {children}
    </div>
  );
}