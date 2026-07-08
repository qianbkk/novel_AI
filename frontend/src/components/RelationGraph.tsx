/**
 * Phase 6：关系图谱 SVG 组件 — 嵌入 WorldBuild 人物阵营 tab 顶部。
 *
 * 布局：
 *   - 中心：主角（role=='主角' 那一格）
 *   - 一圈：其它角色按 role 扇区分布
 *   - 关系边：按 tags/relation 染色（敌对/师徒/暧昧/盟友）
 *
 * 数据源：GET /projects/{pid}/relations/graph
 * 交互：点节点 → onNodeClick(charId) → 跳转到角色卡详情页
 *
 * 不引 d3 / 任何依赖，纯 SVG + 极坐标布局。
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { GraphNode, GraphEdge } from "../types";

interface Props {
  projectId: string;
  onNodeClick?: (characterId: string) => void;
}

const SECTORS = ["主角", "重要配角", "反派", "其他"];

// 边分类标签词库：tags 优先精确匹配（结构化，无歧义）；
// relation 文本仅在 tags 为空时按完整词 fallback（避免子串误判"师"→"律师"/"法师"）。
// 历史 review 备注：之前 `rel.includes("师")` 会把"军师"/"大师兄"也归为师徒。
const HOSTILE_TAGS = new Set(["敌对", "仇恨"]);
const MENTOR_TAGS  = new Set(["师徒"]);
const AMBIGUOUS_TAGS = new Set(["暧昧"]);

const HOSTILE_REL = new Set(["宿敌", "仇人", "敌人", "敌对", "仇敌"]);
const MENTOR_REL  = new Set(["师徒", "师父", "师傅", "徒儿", "徒弟"]);
const AMBIGUOUS_REL = new Set(["暧昧", "恋人", "情人", "爱慕", "暗恋"]);

/**
 * 纯函数：边颜色分类。
 * 返回 CSS class 名后缀，匹配 styles.css 里的 .fg-edge--XXX。
 */
function edgeColorClass(rel: string, tags: string[] | null): string {
  // tags 优先
  if (tags) {
    for (const t of tags) {
      if (HOSTILE_TAGS.has(t)) return "fg-edge--hostile";
      if (MENTOR_TAGS.has(t))  return "fg-edge--mentor";
      if (AMBIGUOUS_TAGS.has(t)) return "fg-edge--ambiguous";
    }
  }
  // fallback: 完整词匹配（不是子串）
  if (rel) {
    if (HOSTILE_REL.has(rel)   || rel.startsWith("宿敌")) return "fg-edge--hostile";
    if (MENTOR_REL.has(rel))   return "fg-edge--mentor";
    if (AMBIGUOUS_REL.has(rel)) return "fg-edge--ambiguous";
  }
  return "fg-edge--ally";
}

/** mutual 边：从边集合里一次扫出所有"被互相关注"的节点 ID。 */
function collectMutualNodes(edges: GraphEdge[]): Set<string> {
  const mutual = new Set<string>();
  for (const e of edges) {
    if (e.mutual) {
      mutual.add(e.from_id);
      mutual.add(e.to_id);
    }
  }
  return mutual;
}

export function RelationGraph({ projectId, onNodeClick }: Props) {
  const [data, setData] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    api.getRelationsGraph(projectId)
      .then(setData)
      .catch((e) => {
        console.error(e);
        setError("加载失败，请检查后端服务");
      })
      .finally(() => setLoading(false));
  }, [projectId]);

  if (loading) return <p className="loading-text">关系图加载中…</p>;
  if (error) return <div className="banner banner-danger">关系图加载失败：{error}</div>;
  if (!data || data.nodes.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state__title">还没有关系数据</div>
        <div className="empty-state__hint">完成世界构建后会自动出现</div>
      </div>
    );
  }

  const W = 760;
  const H = 420;
  const cx = W / 2;
  const cy = H / 2;
  const r = Math.min(W, H) * 0.34;

  // Mutual 检查：一次性扫出所有 mutual 节点（O(E)），
  // 渲染时直接 set lookup（O(1)），避免节点×边的嵌套扫。
  const mutualNodeIds = useMemo(
    () => collectMutualNodes(data.edges),
    [data.edges],
  );

  // 1. 找主角（fallback 到第一个节点）
  const protagonist = data.nodes.find(n => n.role === "主角") || data.nodes[0];
  const others = data.nodes.filter(n => n.id !== protagonist.id);

  // 2. 按 role 扇区分组
  const nodesBySector = new Map<string, GraphNode[]>();
  others.forEach(n => {
    const k = SECTORS.includes(n.role || "") ? (n.role || "其他") : "其他";
    if (!nodesBySector.has(k)) nodesBySector.set(k, []);
    nodesBySector.get(k)!.push(n);
  });

  // 3. 计算每个节点极坐标位置
  const positions = new Map<string, { x: number; y: number; kind: string; role: string | null }>();
  positions.set(protagonist.id, { x: cx, y: cy, kind: "protagonist", role: protagonist.role });

  const totalSlots = others.length || 1;
  let angleIdx = 0;
  SECTORS.slice(1).forEach(sec => {
    const list = nodesBySector.get(sec) || [];
    list.forEach(node => {
      const a = (angleIdx / totalSlots) * Math.PI * 2 - Math.PI / 2;
      positions.set(node.id, {
        x: cx + Math.cos(a) * r,
        y: cy + Math.sin(a) * r,
        kind: sec === "反派" ? "hostile" : "ally",
        role: node.role,
      });
      angleIdx++;
    });
  });

  // 4. 边颜色分类（已提取到模块级函数 edgeColorClass）

  return (
    <div className="faction-graph" style={{ marginBottom: 18 }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ height: "auto", maxHeight: H }}
        role="img"
        aria-label="角色关系图谱"
      >
        {/* 中心淡光 */}
        <defs>
          <radialGradient id="rg-fade" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#6B8AFD" stopOpacity="0.08" />
            <stop offset="100%" stopColor="#6B8AFD" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx={cx} cy={cy} r={r * 0.7} fill="url(#rg-fade)" />

        {/* 外环虚线 */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border-strong)" strokeDasharray="3 6" opacity="0.5" />

        {/* 关系边 */}
        {data.edges.map((e) => {
          const from = positions.get(e.from_id);
          const to = positions.get(e.to_id);
          if (!from || !to) return null;
          const mx = (from.x + to.x) / 2;
          const my = (from.y + to.y) / 2;
          const dx = to.x - from.x;
          const dy = to.y - from.y;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          // 垂线偏移（弧形效果）
          const nx = -dy / len;
          const ny = dx / len;
          const cpX = mx + nx * 16;
          const cpY = my + ny * 16;
          return (
            <path
              key={`${e.from_id}-${e.to_id}`}
              d={`M ${from.x} ${from.y} Q ${cpX} ${cpY} ${to.x} ${to.y}`}
              className={`fg-edge ${edgeColorClass(e.relation, e.tags)}`}
              strokeWidth={Math.max(0.6, (e.intensity || 5) * 0.28)}
              fill="none"
              opacity={0.75}
            />
          );
        })}

        {/* 节点 */}
        {data.nodes.map(n => {
          const pos = positions.get(n.id);
          if (!pos) return null;
          const isProtagonist = pos.kind === "protagonist";
          const onActivate = onNodeClick ? () => onNodeClick(n.id) : undefined;
          return (
            <g
              key={n.id}
              className={`fg-node fg-node--${pos.kind}`}
              onClick={onActivate}
              onKeyDown={onActivate ? (ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  onActivate();
                }
              } : undefined}
              tabIndex={onNodeClick ? 0 : -1}
              role={onNodeClick ? "button" : "img"}
              aria-label={onNodeClick ? `查看 ${n.name} 的角色卡` : `${n.name}${n.role ? `（${n.role}）` : ""}`}
              style={{ cursor: onNodeClick ? "pointer" : "default" }}
            >
              <circle
                cx={pos.x} cy={pos.y}
                r={isProtagonist ? 16 : 9}
                className="fg-node-circle"
              />
              {/* 角色名（上方） */}
              <text
                x={pos.x} y={pos.y - (isProtagonist ? 22 : 14)}
                textAnchor="middle"
                className="fg-node-label"
              >
                {n.name}
              </text>
              {/* role（下方小字） */}
              {n.role && (
                <text
                  x={pos.x} y={pos.y + (isProtagonist ? 30 : 22)}
                  textAnchor="middle"
                  className="fg-node-role"
                >
                  {n.role}
                </text>
              )}
              {/* mutual 标记（中心点）—— 用预计算的 Set lookup */}
              {mutualNodeIds.has(n.id) && (
                <text
                  x={pos.x} y={pos.y + 4}
                  textAnchor="middle"
                  fill="var(--color-bg-0)"
                  fontSize={isProtagonist ? 11 : 8}
                  fontWeight={700}
                >
                  ⇄
                </text>
              )}
            </g>
          );
        })}

        {/* 中心标识 */}
        <text
          x={cx} y={cy + 38}
          textAnchor="middle"
          fill="var(--text-muted)"
          fontFamily="var(--font-display)"
          fontSize="11"
          letterSpacing="0.1em"
        >
          点击节点查看角色卡
        </text>
      </svg>

      {/* 图例 */}
      <div className="fg-legend">
        <LegendDot color="var(--color-accent-strong)" label="主角" />
        <LegendDot color="var(--color-moss)" label="盟友" />
        <LegendDot color="var(--color-stamp)" label="敌对" />
        <LegendLine cls="fg-edge--ally"       label="盟友关系" />
        <LegendLine cls="fg-edge--hostile"    label="敌对关系" />
        <LegendLine cls="fg-edge--mentor"     label="师徒关系" />
        <LegendLine cls="fg-edge--ambiguous"  label="暧昧关系" />
        <span className="fg-legend__hint">边粗 = 强度 0-10</span>
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="fg-legend__dot">
      <span className="fg-legend__dot-circle" style={{ background: color }} />
      {label}
    </span>
  );
}

/** 用现有 fg-edge--XX 类染色（图例与 SVG 边严格一致）。 */
function LegendLine({ cls, label }: { cls: string; label: string }) {
  return (
    <span className="fg-legend__line">
      <svg width="16" height="6" aria-hidden="true">
        <line x1="0" y1="3" x2="16" y2="3" className={`fg-edge ${cls}`} strokeWidth="2" />
      </svg>
      {label}
    </span>
  );
}

