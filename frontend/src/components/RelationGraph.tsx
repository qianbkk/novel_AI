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
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { GraphNode, GraphEdge } from "../types";

interface Props {
  projectId: string;
  onNodeClick?: (characterId: string) => void;
}

const SECTORS = ["主角", "重要配角", "反派", "其他"];

export function RelationGraph({ projectId, onNodeClick }: Props) {
  const [data, setData] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    api.getRelationsGraph(projectId)
      .then(setData)
      .catch((e) => setError(String(e)))
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

  // 4. 边颜色分类
  const edgeColorClass = (rel: string, tags: string[] | null): string => {
    const tagSet = new Set(tags || []);
    if (tagSet.has("敌对") || tagSet.has("仇恨") || rel.includes("仇") || rel.includes("敌")) {
      return "fg-edge--hostile";
    }
    if (tagSet.has("师徒") || rel.includes("师")) return "fg-edge--mentor";
    if (tagSet.has("暧昧") || rel.includes("恋") || rel.includes("爱")) {
      return "fg-edge--ambiguous";
    }
    return "fg-edge--ally";
  };

  return (
    <div className="faction-graph" style={{ marginBottom: 18 }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
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
        {data.edges.map((e, i) => {
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
              key={i}
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
          return (
            <g
              key={n.id}
              className={`fg-node fg-node--${pos.kind}`}
              onClick={() => onNodeClick?.(n.id)}
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
              {/* mutual 标记（中心点） */}
              {data.edges.some(e => e.mutual && (
                (e.from_id === n.id && positions.get(e.to_id)) ||
                (e.to_id === n.id && positions.get(e.from_id))
              )) && (
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
      <div style={{
        display: "flex", gap: 14, padding: "8px 14px",
        fontSize: 11, color: "var(--color-fg-3)",
        borderTop: "1px solid var(--color-border-1)",
        flexWrap: "wrap",
      }}>
        <LegendDot color="var(--color-accent-strong)" label="主角" />
        <LegendDot color="var(--color-moss)" label="盟友" />
        <LegendDot color="var(--color-stamp)" label="敌对" />
        <span><span style={{ display: "inline-block", width: 16, height: 2, background: "var(--color-moss)", verticalAlign: "middle", marginRight: 4 }}/>盟友关系</span>
        <span><span style={{ display: "inline-block", width: 16, height: 2, background: "var(--color-stamp)", verticalAlign: "middle", marginRight: 4 }}/>敌对关系</span>
        <span><span style={{ display: "inline-block", width: 16, height: 2, background: "var(--color-accent)", verticalAlign: "middle", marginRight: 4 }}/>师徒关系</span>
        <span><span style={{ display: "inline-block", width: 16, height: 2, background: "var(--color-warn)", borderTop: "2px dashed var(--color-warn)", verticalAlign: "middle", marginRight: 4 }}/>暧昧关系</span>
        <span>边粗 = 强度 0-10</span>
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{
        display: "inline-block", width: 10, height: 10, borderRadius: "50%",
        background: color,
      }}/>
      {label}
    </span>
  );
}