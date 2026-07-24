/**
 * ErrorBoundary — 路由级错误兜底。
 *
 * React 没有内建的 try/catch 边界；某子组件渲染时抛异常（比如 schema 漂移导致
 * 列表渲染失败）会冒泡到根，整个 SPA 白屏。
 *
 * 这个 class 组件包住 <Routes>：任何子组件抛错就降级到友好的错误页，
 * 显示错误消息 + "返回首页 / 重试 / 复制调试" 三个动作，不再白屏。
 *
 * 性能：仅当子组件渲染时抛错才触发；不增加正常路径的开销。
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useToast } from "./Toast";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  componentStack: string;
  path: string;
  ts: string;
}

function CopyDebugButton({ info }: { info: Omit<State, "hasError"> }) {
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
    <button
      type="button"
      className="btn btn-ghost"
      onClick={copy}
      aria-label="复制错误调试信息"
    >
      📋 复制调试信息
    </button>
  );
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = {
    hasError: false,
    error: null,
    componentStack: "",
    path: "",
    ts: "",
  };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // 路由切换时也要记录 path，方便定位
    const path = typeof window !== "undefined" ? window.location.pathname : "";
    this.setState({
      componentStack: info.componentStack || "",
      path,
      ts: new Date().toISOString(),
    });
    // 走 console.error 保留原始堆栈，开发者可从 devtools 看到
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] caught:", error, info);
  }

  reset = (): void => {
    this.setState({
      hasError: false,
      error: null,
      componentStack: "",
      path: "",
      ts: "",
    });
  };

  override render(): ReactNode {
    if (!this.state.hasError) return this.props.children;
    const { error, path, ts, componentStack } = this.state;
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-header__title">⚠ 页面出错了</h1>
            <div className="page-header__sub">
              路径 <code className="text-mono">{path || "?"}</code>
              {ts ? ` · ${ts}` : ""}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="empty-state">
            <div className="empty-state__icon" aria-hidden="true">
              <svg width="44" height="44" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="1.4"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                <line x1="12" y1="9" x2="12" y2="13" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
            </div>
            <div className="empty-state__title">渲染时捕获到未处理异常</div>
            <div className="empty-state__hint" style={{ maxWidth: 560, textAlign: "center" }}>
              整个页面已降级到此错误页。下方有错误摘要，复制调试信息给开发者可帮助定位。
            </div>
            <div className="empty-state__action" style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
              <Link to="/" className="btn btn-primary" aria-label="返回首页">
                ← 返回首页
              </Link>
              <button
                type="button"
                className="btn"
                onClick={this.reset}
                aria-label="重试当前页"
                title="清掉错误状态，重新渲染上一次出错的页面"
              >
                ↻ 重试
              </button>
              <CopyDebugButton info={{ error, path, ts, componentStack }} />
            </div>
          </div>
        </div>
        <details className="card" style={{ marginTop: 12 }}>
          <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--color-fg-2)" }}>
            技术细节（开发用）
          </summary>
          <pre
            className="text-mono"
            style={{
              fontSize: 11.5,
              padding: 12,
              margin: "8px 0 0",
              background: "var(--color-bg-1, rgba(255,255,255,0.04))",
              borderRadius: 6,
              overflow: "auto",
              maxHeight: 280,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
{`name:    ${error?.name ?? ""}
message: ${error?.message ?? ""}

componentStack:
${(componentStack || "").split("\n").slice(0, 12).join("\n")}
`}
          </pre>
        </details>
      </div>
    );
  }
}
