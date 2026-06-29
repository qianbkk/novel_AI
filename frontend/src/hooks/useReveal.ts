import { useEffect } from "react";

/**
 * useReveal：在挂载时把所有 .reveal 元素加入 IntersectionObserver，
 * 进入视口时加上 .is-visible 触发 CSS 过渡。
 *
 * 用法：useReveal(rootRef);  // rootRef.current 指向滚动容器
 *      或 useReveal();       // 监听 document.body
 */
export function useReveal<T extends Element = HTMLElement>(
  rootRef?: React.RefObject<T | null>,
  options: IntersectionObserverInit = { threshold: 0.12, rootMargin: "0px 0px -40px 0px" },
) {
  useEffect(() => {
    const root = rootRef?.current ?? document.body;
    if (!root) return;
    const targets = (root as Element).querySelectorAll<HTMLElement>(".reveal");
    if (targets.length === 0) return;

    // 立即给已经在视口里的元素加上 is-visible（首屏不闪烁）
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          e.target.classList.add("is-visible");
          io.unobserve(e.target);
        }
      }
    }, { root: root === document.body ? undefined : (root as Element), ...options });

    targets.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [rootRef, options]);
}
