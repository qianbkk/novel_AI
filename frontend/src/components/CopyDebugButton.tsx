/**
 * CopyDebugButton — 2026-07-25 抽离（修 P19 /simplify Reuse 维度）。
 *
 * 通用"复制调试信息"按钮：把 {path / search / ts / ...} 拼成 JSON
 * 复制到剪贴板，成功 / 失败 / 不支持三态给 toast 反馈。
 *
 * 之前 NotFound.tsx 和 ErrorBoundary.tsx 各自 16 行复制逻辑
 * 几乎逐行相同，本组件让两边只传 payload。
 */
import { useToast } from "./Toast";

export function CopyDebugButton({
  info,
  label = "📋 复制调试信息",
  className = "btn btn-ghost",
}: {
  info: Record<string, unknown>;
  label?: string;
  className?: string;
}) {
  const toast = useToast();
  function copy() {
    const text = JSON.stringify(
      { ...info, ua: navigator.userAgent.slice(0, 120) },
      null,
      2,
    );
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text)
        .then(() => toast.success("调试信息已复制", "可贴给开发者"))
        .catch(() => toast.warn("复制失败", "请手动截屏"));
    } else {
      toast.warn("当前浏览器不支持剪贴板", "请手动截屏");
    }
  }
  return (
    <button type="button" className={className} onClick={copy} aria-label={label}>
      {label}
    </button>
  );
}
