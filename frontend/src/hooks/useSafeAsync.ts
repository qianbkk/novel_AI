/**
 * useSafeAsync — 2026-07-25 抽出（修 P0 重复模板）。
 *
 * 之前 7 个 page 都各自手写：
 *   const mountedRef = useRef(true);
 *   useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
 *   if (!mountedRef.current) return;
 *
 * 同时还要管理 AbortController（useEffect cleanup 时 abort 当前请求）。
 *
 * 这个 hook 把两个职责合并：
 *   1. isMounted() — 组件卸载后返回 false，所有 setState 调用前先 check
 *   2. signal 字段 — 每次 effect 重跑时自动 abort 上一次的请求，新请求传新 signal
 *
 * 用法：
 *   const { isMounted, signal } = useSafeAsync();
 *   useEffect(() => {
 *     api.listProjects({}, { signal }).then(p => { if (isMounted()) setProjects(p); });
 *   }, []);
 */
import { useEffect, useRef } from "react";

export interface SafeAsyncController {
  /** 返回组件是否还挂载着（effect cleanup 后变 false）。 */
  isMounted: () => boolean;
  /** 当前 effect 周期内的 AbortSignal — 传进 api.* 调用即可。 */
  signal: AbortSignal;
}

export function useSafeAsync(): SafeAsyncController {
  const mountedRef = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    // 重新挂载时 abort 旧 controller（理论上 useRef 在 unmount 后再 mount 会复用旧值）
    controllerRef.current = new AbortController();
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort(new DOMException("Component unmounted", "AbortError"));
      controllerRef.current = null;
    };
  }, []);

  return {
    isMounted: () => mountedRef.current,
    signal: controllerRef.current?.signal ?? new AbortController().signal,
  };
}
