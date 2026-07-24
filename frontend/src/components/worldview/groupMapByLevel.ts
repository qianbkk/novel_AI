/**
 * groupMapByLevel — 2026-07-25 抽离（修 P1-1 短板巨型 page 拆解）
 *
 * 把 MapNode 按 level 分组显示（"world" / "continent" / "province"）。
 * 之前是 WorldBuild.tsx 内嵌私有工具函数，WorldviewTab/MapTab 都用。
 */
import type { MapNode } from "../../types";

export type GroupedNodes = { level: string; nodes: MapNode[] }[];

export function groupMapByLevel(nodes: MapNode[]): GroupedNodes {
  const seen = new Map<string, MapNode[]>();
  for (const n of nodes) {
    const arr = seen.get(n.level) || [];
    arr.push(n);
    seen.set(n.level, arr);
  }
  // 排序：按 level 字串首字母排（粗略，但够用）
  return Array.from(seen.entries())
    .map(([level, ns]) => ({ level, nodes: ns }))
    .sort((a, b) => a.level.localeCompare(b.level));
}
