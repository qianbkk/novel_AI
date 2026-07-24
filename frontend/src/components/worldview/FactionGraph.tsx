/**
 * FactionGraph — 2026-07-25 抽离（修 P1-1 短板巨型 page 拆解）
 *
 * 势力关系 SVG 图（环形布局 + 关系边）。
 * 不引入 d3 / 任何依赖；用三角函数放置节点，再用弧线画关系。
 * 之前是 WorldBuild.tsx 内嵌私有组件，拆出来供"人物阵营"tab 复用。
 */
export function FactionGraph({
  factions,
  characters,
}: {
  factions: { id: string; name: string }[];
  characters: { id: string; name: string; role: string | null }[];
}) {
  const W = 600;
  const H = 320;
  const cx = W / 2;
  const cy = H / 2;
  const r = Math.min(W, H) * 0.36;

  // 主体是 factions；主要人物节点围绕
  const mainChars = characters.slice(0, Math.max(6, factions.length * 2));

  const factionNodes = factions.map((f, i) => {
    const a = (i / Math.max(1, factions.length)) * Math.PI * 2 - Math.PI / 2;
    return {
      id: f.id,
      name: f.name,
      x: cx + Math.cos(a) * (r * 0.55),
      y: cy + Math.sin(a) * (r * 0.55),
    };
  });

  const charNodes = mainChars.map((c, i) => {
    const a = (i / Math.max(1, mainChars.length)) * Math.PI * 2 - Math.PI / 2;
    return {
      id: c.id,
      name: c.name,
      role: c.role || "",
      x: cx + Math.cos(a) * r,
      y: cy + Math.sin(a) * r,
    };
  });

  // 简易关联边：把字符按 role/位置画回最近的 faction
  type Edge = { from: typeof charNodes[number]; to: typeof factionNodes[number]; kind: "ally" | "hostile" };
  const edges: Edge[] = [];
  charNodes.forEach((c, i) => {
    const f = factionNodes[i % Math.max(1, factionNodes.length)];
    if (!f) return;
    edges.push({ from: c, to: f, kind: i % 5 === 0 ? "hostile" : "ally" });
  });

  return (
    <div className="faction-graph" style={{ marginBottom: 14 }}>
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
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border-strong)" strokeDasharray="3 6" opacity="0.5" />
        <circle cx={cx} cy={cy} r={r * 0.55} fill="none" stroke="var(--border-strong)" opacity="0.35" />
        {/* 关联边 */}
        {edges.map((e, i) => {
          // 用二次贝塞尔做一个轻轻弧形
          const mx = (e.from.x + e.to.x) / 2;
          const my = (e.from.y + e.to.y) / 2;
          const dx = e.to.x - e.from.x;
          const dy = e.to.y - e.from.y;
          const len = Math.sqrt(dx * dx + dy * dy);
          // 垂线
          const nx = -dy / len;
          const ny = dx / len;
          const cpX = mx + nx * 12;
          const cpY = my + ny * 12;
          return (
            <path
              key={i}
              d={`M ${e.from.x} ${e.from.y} Q ${cpX} ${cpY} ${e.to.x} ${e.to.y}`}
              className={`fg-edge ${e.kind === "hostile" ? "fg-edge--hostile" : "fg-edge--ally"}`}
            />
          );
        })}
        {/* 字符节点 */}
        {charNodes.map((c) => (
          <g key={c.id} className="fg-node">
            <circle cx={c.x} cy={c.y} r={4} className="fg-node-circle" />
            <text x={c.x} y={c.y - 8} textAnchor="middle" className="fg-node-label">{c.name}</text>
          </g>
        ))}
        {/* 势力节点（更大） */}
        {factionNodes.map((f) => (
          <g key={f.id} className="fg-node">
            <circle cx={f.x} cy={f.y} r={12} className="fg-node-circle" stroke="var(--accent-strong)" />
            <text x={f.x} y={f.y + 4} textAnchor="middle" fill="var(--accent-strong)" fontFamily="var(--font-display)" fontSize="11" fontWeight={600}>
              {f.name.slice(0, 2)}
            </text>
            <text x={f.x} y={f.y + 24} textAnchor="middle" className="fg-node-label">{f.name}</text>
          </g>
        ))}
        {/* 中心标识 */}
        <text x={cx} y={cy + 4} textAnchor="middle" fill="var(--text-muted)" fontFamily="var(--font-display)" fontSize="11" letterSpacing="0.1em">世 界</text>
      </svg>
    </div>
  );
}
