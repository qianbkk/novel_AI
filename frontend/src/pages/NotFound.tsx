/**
 * NotFound — 404 兜底页面。
 *
 * 之前 App.tsx 没有 path="*" 兜底路由，输错 URL 直接白屏。
 * 现在显示一个友好页面，给"返回项目 / 返回首页 / 复制 URL 给开发者"三个动作。
 */
import { Link, useLocation, useNavigate } from "react-router-dom";
import { CopyDebugButton } from "../components/CopyDebugButton";

export default function NotFound() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">404 · 页面没找到</h1>
          <div className="page-header__sub">
            路径 <code className="text-mono">{location.pathname}</code> 不在已知的路由表里
          </div>
        </div>
      </div>

      <div className="card">
        <div className="empty-state">
          <div className="empty-state__icon" aria-hidden="true">
            <svg width="44" height="44" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.4"
              strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <path d="M8 15s1.5-2 4-2 4 2 4 2" />
              <line x1="9" y1="9" x2="9.01" y2="9" />
              <line x1="15" y1="9" x2="15.01" y2="9" />
            </svg>
          </div>
          <div className="empty-state__title">这条路径我们没收录</div>
          <div className="empty-state__hint" style={{ maxWidth: 480, textAlign: "center" }}>
            可能是链接拼错，或者老版本 URL 已经改版。下面的按钮能帮你找回方向。
          </div>
          <div className="empty-state__action" style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
            <Link to="/" className="btn btn-primary" aria-label="返回首页项目列表">
              ← 返回项目列表
            </Link>
            <button
              type="button"
              className="btn"
              onClick={() => navigate(-1)}
              aria-label="返回上一页"
            >
              ← 上一页
            </button>
            <CopyDebugButton
              info={{
                pathname: location.pathname,
                search: location.search,
                hash: location.hash,
                ts: new Date().toISOString(),
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
