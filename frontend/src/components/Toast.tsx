import { useEffect, useState, useCallback, createContext, useContext, useRef } from "react";

/**
 * Toast — 轻量通知组件
 * 设计目标：3s 自动消失 / 屏幕底部居中 / 可手动关闭 / 键盘可访问
 * 状态:  success | info | warn | error
 */

export type ToastKind = "success" | "info" | "warn" | "error";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
}

interface ToastContextValue {
  push: (t: Omit<ToastItem, "id">) => void;
  success: (title: string, description?: string) => void;
  info:    (title: string, description?: string) => void;
  warn:    (title: string, description?: string) => void;
  error:   (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

const KIND_TO_CLASS: Record<ToastKind, string> = {
  success: "toast--success",
  info:    "toast--info",
  warn:    "toast--warn",
  error:   "toast--error",
};

const DEFAULT_DURATION = 3200;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (t: Omit<ToastItem, "id">) => {
      const id = idRef.current++;
      const item: ToastItem = { ...t, id };
      setItems((prev) => [...prev, item]);
      window.setTimeout(() => dismiss(id), DEFAULT_DURATION);
    },
    [dismiss],
  );

  const api: ToastContextValue = {
    push,
    success: (title, description) => push({ kind: "success", title, description }),
    info:    (title, description) => push({ kind: "info",    title, description }),
    warn:    (title, description) => push({ kind: "warn",    title, description }),
    error:   (title, description) => push({ kind: "error",   title, description }),
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" role="region" aria-label="通知" aria-live="polite">
        {items.map((t) => (
          <ToastView key={t.id} item={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastView({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  // ESC 关闭焦点上的 toast
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onDismiss();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onDismiss]);

  return (
    <div className={`toast ${KIND_TO_CLASS[item.kind]}`} role="status">
      <div className="toast__icon" aria-hidden="true">
        {item.kind === "success" && "✓"}
        {item.kind === "info"    && "i"}
        {item.kind === "warn"    && "!"}
        {item.kind === "error"   && "×"}
      </div>
      <div className="toast__body">
        <div className="toast__title">{item.title}</div>
        {item.description && <div className="toast__desc">{item.description}</div>}
      </div>
      <button
        className="toast__close"
        onClick={onDismiss}
        aria-label="关闭通知"
        type="button"
      >
        ×
      </button>
    </div>
  );
}
