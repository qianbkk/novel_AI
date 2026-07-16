import { useEffect, useRef, ReactNode } from "react";

/**
 * Dialog — 通用 <dialog> 弹层包装
 * 用法：<Dialog open title="…" onClose={…}>content</Dialog>
 *
 * 用浏览器原生 <dialog> + showModal() 实现，自动获得：
 * - ESC 关闭
 * - ::backdrop 样式
 * - 焦点陷阱
 */

interface DialogProps {
  open: boolean;
  title?: string;
  sub?: string;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  /** 修订 2026-07-16：宽屏模式（章节详情用），自动加 dialog-wide class。 */
  wide?: boolean;
  /** 额外 className 拼到 <dialog> 上，用来切换宽度（如 dialog-wide）。 */
  className?: string;
}

export function Dialog({ open, title, sub, onClose, children, actions, wide, className }: DialogProps) {
  const ref = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      el.showModal();
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);

  // 监听 dialog 原生的 close 事件（ESC 触发）
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    function handleClose() { onClose(); }
    el.addEventListener("close", handleClose);
    return () => el.removeEventListener("close", handleClose);
  }, [onClose]);

  const finalClass = ["ink-dialog", wide ? "dialog-wide" : "", className || ""].filter(Boolean).join(" ");
  return (
    <dialog ref={ref} className={finalClass} aria-labelledby="ink-dialog-title">
      {title && <h3 id="ink-dialog-title" className="ink-dialog__title">{title}</h3>}
      {sub && <p className="ink-dialog__sub">{sub}</p>}
      <div className="ink-dialog__body">{children}</div>
      {actions && <div className="ink-dialog__actions">{actions}</div>}
    </dialog>
  );
}
