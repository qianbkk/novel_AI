/**
 * EmptyTab — 2026-07-25 抽离（修 P1-1 短板巨型 page 拆解）
 *
 * 通用空状态组件：tab 数据为空时显示 + 提供"重新构建" CTA。
 * 之前是 WorldBuild.tsx 内嵌私有组件，拆出来给所有 worldview 子组件复用。
 */
export function EmptyTab({
  icon, title, hint, actionLabel, onAction,
}: {
  icon: string;
  title: string;
  hint: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="empty-state empty-state--with-action">
      <div className="empty-state__icon" style={{ fontSize: 36 }}>{icon}</div>
      <div className="empty-state__title">{title}</div>
      <div className="empty-state__hint" style={{ maxWidth: 420, textAlign: "center" }}>{hint}</div>
      {actionLabel && onAction && (
        <button className="btn btn-primary" onClick={onAction} style={{ marginTop: 16 }}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}
