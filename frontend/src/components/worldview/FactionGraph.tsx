/**
 * FactionGraph — 2026-07-25 抽离（修 P1-1 短板巨型 page 拆解）。
 *
 * 势力关系 SVG 图（环形布局 + 关系边）。
 *
 * 2026-07-27 修真实数据链：
 *   之前 props 拿 factions/characters 然后**自己合成边**（i%5 决定敌对/盟友），
 *   截图呈现的"敌对/盟友"与数据库事实不符——用户按图索骥会被误导。
 *   现在改为自取 `api.getRelationsGraph(projectId)`，筛 character↔faction
 *   与 faction↔faction 的真实边，按 relation 词染色渲染。
 *
 * 不引入 d3 / 任何依赖；用三角函数放置节点，再用弧线画关系。
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { GraphEdge, GraphNode, RelationGraph } from "../../types";

type EdgeKind = "ally" | "hostile" | "neutral";

const HOSTILE_TAGS = new Set(["敌对", "宿敌", "宿怨", "仇人", "对头", "宿仇"]);
const ALLY_TAGS = new Set(["盟友", "友好", "同盟", "合作", "友善"]);

/** 把边归到三类之一；未匹配归 neutral。
 * 优先看 tags_json（系统内分类），回退 relation 字符串里的关键词，
 * 实在没有就 neutral（不能伪造敌对/盟友）。 */
function classifyEdge(edge: GraphEdge): EdgeKind {
  for (const t of edge.tags || []) {
    if (HOSTILE_TAGS.has(t)) return "hostile";
    if (ALLY_TAGS.has(t)) return "ally";
  }
  const r = (edge.relation || "").trim();
  for (const k of HOSTILE_TAGS) if (r.includes(k)) return "hostile";
  for (const k of ALLY_TAGS) if (r.includes(k)) return "ally";
  return "neutral";
}

function isFactionEdge(e: GraphEdge): boolean {
  // 任一端是 faction 就算 faction 关系
  return e.from_type === "faction" || e.to_type === "faction";
}

export function FactionGraph({
  projectId,
  factionNames,
}: {
  projectId: string;
  /** 父级传入的势力名集合（用于友好节点渲染）；实际边数据由组件自取 */
  factionNames: string[];
}) {
  const W = 600;
  const H = 320;
  const cx = W / 2;
  const cy = H / 2;
  const r = Math.min(W, H) * 0.36;

  const [graph, setGraph] = useState<RelationGraph | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    api.getRelationsGraph(projectId)
      .then((g) => { if (!cancelled) { setGraph(g); setError(null); } })
      .catch((e) => {
        // 失败要响亮：图谱是验收项，假数据比无图更糟
        if (!cancelled) {
          setError(typeof e === "string" ? e : String(e));
          console.error("FactionGraph: 加载关系图失败", e);
        }
      });
    return () => { cancelled = true; };
  }, [projectId]);

  if (error) {
    return (
      <div className="faction-graph" style={{ marginBottom: 14 }} data-testid="faction-graph-error">
        <div className="banner banner-danger">势力关系图加载失败：{error}</div>
      </div>
    );
  }
  if (!graph) {
    return (
      <div className="faction-graph" style={{ marginBottom: 14 }} data-testid="faction-graph-loading">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          <text x={cx} y={cy + 4} textAnchor="middle" fill="var(--text-muted)"
                fontFamily="var(--font-display)" fontSize={11} letterSpacing="0.1em">
            加载中…
          </text>
        </svg>
      </div>
    );
  }

  const factionNodes = graph.nodes.filter((n) => n.role_kind === "faction");
  const charNodes    = graph.nodes.filter((n) => n.role_kind === "character");

  // 没势力数据时给空态 —— 之前会强行画 6 个孤立圆点
  if (factionNodes.length === 0) {
    return (
      <div className="faction-graph" style={{ marginBottom: 14 }} data-testid="faction-graph-empty">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          <text x={cx} y={cy + 4} textAnchor="middle" fill="var(--text-muted)"
                fontFamily="var(--font-display)" fontSize={12}>
            暂无势力数据
          </text>
        </svg>
      </div>
    );
  }

  // 只渲染至少一端是 faction 的边 —— 不画纯 character-character 的边
  // （那是 RelationGraph 的工作，这里专注势力与归属关系）
  const factionEdges = graph.edges.filter(isFactionEdge);

  // 名称列表（显示用）：优先用关系图节点的 name，回退到父级传入的 factionNames
  const factionDisplay = factionNodes.length
    ? factionNodes
    : factionNames.map((n, i) => ({ id: `name-${i}`, name: n, role: null, role_kind: "faction" }));

  const factionPositions = factionDisplay.map((f, i) => {
    const a = (i / Math.max(1, factionDisplay.length)) * Math.PI * 2 - Math.PI / 2;
    return {
      id: f.id,
      name: f.name,
      x: cx + Math.cos(a) * (r * 0.55),
      y: cy + Math.sin(a) * (r * 0.55),
    };
  });

  // 与某个 faction 有关联的角色才上圈，避免无关联的角色被强行画入孤立点
  const relatedCharIds = new Set<string>();
  for (const e of factionEdges) {
    if (e.from_type === "character") relatedCharIds.add(e.from_id);
    if (e.to_type   === "character") relatedCharIds.add(e.to_id);
  }
  const charPositions = charNodes
    .filter((c) => relatedCharIds.has(c.id))
    .map((c, i) => {
      const a = (i / Math.max(1, relatedCharIds.size)) * Math.PI * 2 - Math.PI / 2;
      return {
        id: c.id,
        name: c.name,
        role: c.role || "",
        x: cx + Math.cos(a) * r,
        y: cy + Math.sin(a) * r,
      };
    });

  function posById(id: string): { x: number; y: number } | undefined {
    return factionPositions.find((p) => p.id === id)
      || charPositions.find((p) => p.id === id);
  }

  return (
    <div className="faction-graph" style={{ marginBottom: 14 }} data-testid="faction-graph">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="fg-fade" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#6B8AFD" stopOpacity="0.06" />
            <stop offset="100%" stopColor="#6B8AFD" stopOpacity="0" />
          </radialGradient>
        </defs>
        {/* 中心淡光 */}
        <circle cx={cx} cy={cy} r={r * 0.7} fill="url(#fg-fade)" />
        {/* 外环 */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border-strong)"
                strokeDasharray="3 6" opacity="0.5" />
        <circle cx={cx} cy={cy} r={r * 0.55} fill="none" stroke="var(--border-strong)" opacity="0.35" />

        {/* 真实关系边 */}
        {factionEdges.map((e, i) => {
          const from = posById(e.from_id);
          const to   = posById(e.to_id);
          if (!from || !to) return null;  // 缺节点静默丢，与 RelationGraph 同模式
          const kind = classifyEdge(e);
          const mx = (from.x + to.x) / 2;
          const my = (from.y + to.y) / 2;
          const dx = to.x - from.x;
          const dy = to.y - from.y;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          const nx = -dy / len;
          const ny = dx / len;
          const cpX = mx + nx * 12;
          const cpY = my + ny * 12;
          return (
            <g key={`e-${e.from_id}-${e.to_id}-${i}`} data-testid="faction-edge">
              <path
                d={`M ${from.x} ${from.y} Q ${cpX} ${cpY} ${to.x} ${to.y}`}
                className={`fg-edge fg-edge--${kind}`}
              />
              <title>{e.relation}{e.intensity != null ? `（强度 ${e.intensity}）` : ""}</title>
            </g>
          );
        })}

        {/* 字符节点（仅显示与势力有关联的角色） */}
        {charPositions.map((c) => (
          <g key={c.id} className="fg-node">
            <circle cx={c.x} cy={c.y} r={4} className="fg-node-circle" />
            <text x={c.x} y={c.y - 8} textAnchor="middle" className="fg-node-label">{c.name}</text>
          </g>
        ))}

        {/* 势力节点（更大） */}
        {factionPositions.map((f) => (
          <g key={f.id} className="fg-node">
            <circle cx={f.x} cy={f.y} r={12} className="fg-node-circle" stroke="var(--accent-strong)" />
            <text x={f.x} y={f.y + 4} textAnchor="middle" fill="var(--accent-strong)"
                  fontFamily="var(--font-display)" fontSize={11} fontWeight={600}>
              {f.name.slice(0, 2)}
            </text>
            <text x={f.x} y={f.y + 24} textAnchor="middle" className="fg-node-label">{f.name}</text>
          </g>
        ))}

        {/* 中心标识 */}
        <text x={cx} y={cy + 4} textAnchor="middle" fill="var(--text-muted)"
              fontFamily="var(--font-display)" fontSize={11} letterSpacing="0.1em">世 界</text>
      </svg>
      {/* 图例：让"敌对/盟友/中性"的染色可被肉眼对照 */}
      {factionEdges.length > 0 && (
        <div className="fg-legend" style={{ display: "flex", gap: 12, fontSize: 12, marginTop: 4 }}>
          <span><i style={{ background: "var(--color-stamp)" }} /> 敌对</span>
          <span><i style={{ background: "var(--color-moss)" }} /> 盟友</span>
          <span><i style={{ background: "var(--color-fg-4)" }} /> 中性</span>
          <span className="text-faint" style={{ marginLeft: "auto" }}>
            {factionEdges.length} 条关系边
          </span>
        </div>
      )}
    </div>
  );
}

// 之前 props 形式 `{ factions, characters }` 已废弃，避免误传：
//   - 测试里若还在传，TypeScript 会编译失败，提示升级。
//   - 历史引用通过 type 兼容层允许一次：父级已迁移到传 projectId + factionNames。
export type FactionGraphLegacyProps = {
  factions: { id: string; name: string }[];
  characters: { id: string; name: string; role: string | null }[];
};