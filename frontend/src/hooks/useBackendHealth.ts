/** useBackendHealth.ts - 2026-08-18

每 5 秒探测后端 /health 端点，给 sidebar 一个实时健康灯。

设计动机（用户报告 #6：WorldBuild 看不到东西，怀疑后端没起来或前端路径错）：
前端从来没有"后端是否在跑"的可视化 — 即便 /projects 报 401，
也得用户自己去对错误消息脑补 "是不是后端挂了"。

修法：
- 周期探测 /health，3 态指示（绿 / 黄 / 灰）
- 失败时 sidebar 显示一个小 banner 提示后端未响应
- 不做启动/停止按钮（dev.bat 的事）— 但提供"打开 dev.bat"快捷入口
   （不暴露 shell 命令调用，避免误杀：CLAUDE.md「失败要响亮，但不替用户决策」）

CLAUDE.md 红线：探测只读 health 端点；不调用写接口；不阻塞 UI。
*/

import { useEffect, useState } from "react";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || "http://localhost:8132";

export type BackendState =
  | { kind: "checking" }
  | { kind: "up"; latencyMs: number | null }
  | { kind: "down"; error: string };

interface Options {
  intervalMs?: number;
  enabled?: boolean;
}

export function useBackendHealth({ intervalMs = 5000, enabled = true }: Options = {}): BackendState {
  const [state, setState] = useState<BackendState>({ kind: "checking" });

  useEffect(() => {
    if (!enabled) {
      setState({ kind: "checking" });
      return;
    }
    let cancelled = false;

    async function probe() {
      const t0 = Date.now();
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 3000);
        const r = await fetch(`${API_BASE}/health`, {
          method: "GET",
          cache: "no-store",
          signal: ctrl.signal,
        });
        clearTimeout(timer);
        if (cancelled) return;
        if (r.ok) {
          setState({ kind: "up", latencyMs: Date.now() - t0 });
        } else {
          setState({ kind: "down", error: `HTTP ${r.status}` });
        }
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "down", error: String(e) });
      }
    }

    probe();
    const id = setInterval(probe, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs, enabled]);

  return state;
}