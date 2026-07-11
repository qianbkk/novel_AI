/**
 * LoginDialog.tsx — Phase 4 多用户认证 UI
 *
 * 设计原则（与 backend/app/auth.py:Phase 3 memo 一致）：
 *   - 单租户本地使用仍是默认场景：dev 模式下 dialog 通常不强制弹出。
 *   - 用户可以选 register / login / logout 三个动作。
 *   - production 模式（NOVEL_PRODUCTION=1 后端开启）必须先 register 或 login。
 *   - 401 时通过 window event 'novel_ai:auth_required' 触发，但 dev 模式下后端不返 401，
 *     所以这个 dialog 主要靠用户主动点登录按钮触发。
 *
 * 触发源：
 *   - 用户主动点顶部 "登录" 按钮
 *   - 后端 401（仅 production 模式）
 *   - localStorage token 失效（meOrNull 返回 null）
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Props {
  /** 控制显隐。 */
  open: boolean;
  /** 关闭 dialog（cancel）。成功登录自动关，不需要 onClose 收到 closed 信号。 */
  onClose: () => void;
  /** 登录 / register 成功后回调（用于父组件更新 user 状态）。 */
  onAuthed: (email: string) => void;
}

export function LoginDialog({ open, onClose, onAuthed }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // 每次打开 reset 字段
  useEffect(() => {
    if (open) {
      setPassword("");
      setErrorMsg(null);
      setSubmitting(false);
    }
  }, [open, mode]);

  if (!open) return null;

  const submit = async () => {
    setErrorMsg(null);
    setSubmitting(true);
    try {
      const data = mode === "register"
        ? await api.register({ email: email.trim(), password, display_name: displayName.trim() || null })
        : await api.login({ email: email.trim(), password });
      onAuthed(data.user.email);
      onClose();
    } catch (e) {
      const msg = (e as Error).message;
      // 后端 401 消息里 "邮箱或密码不对" — 比"后端拼的英文错误"温和
      if (msg.includes("401")) {
        setErrorMsg("邮箱或密码不对");
      } else if (msg.includes("409")) {
        setErrorMsg("邮箱已注册，换一个试试");
      } else if (msg.includes("422")) {
        setErrorMsg("输入有问题（密码至少 8 位 / email 含 @）");
      } else {
        setErrorMsg(msg);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !submitting) submit();
  };

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg)", borderRadius: 8, padding: 24,
          width: 360, maxWidth: "92vw",
          boxShadow: "0 8px 32px rgba(0,0,0,0.3)",
        }}
      >
        <h2 style={{ marginTop: 0 }}>
          {mode === "register" ? "注册账号" : "登录"}
        </h2>

        <p style={{ fontSize: 13, opacity: 0.7, marginTop: 0 }}>
          {mode === "register"
            ? "首次注册的用户会自动获得所有未认领的项目（owner_id=NULL）。"
            : "登录后只能用自己创建的项目。dev 模式可跳过登录。"}
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <input
            type="email"
            placeholder="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
            style={inputStyle}
          />
          <input
            type="password"
            placeholder="密码（至少 8 位）"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={onKey}
            disabled={submitting}
            style={inputStyle}
          />
          {mode === "register" && (
            <input
              type="text"
              placeholder="昵称（可选）"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              onKeyDown={onKey}
              disabled={submitting}
              style={inputStyle}
            />
          )}
        </div>

        {errorMsg && (
          <div style={{ color: "#d33", fontSize: 13, marginTop: 12 }}>
            {errorMsg}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 18 }}>
          <button
            onClick={submit}
            disabled={submitting || !email || password.length < 8}
            style={{
              flex: 1,
              padding: "10px 0",
              background: "var(--accent, #2563eb)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: submitting ? "not-allowed" : "pointer",
              opacity: submitting ? 0.6 : 1,
            }}
          >
            {submitting ? "提交中…" : (mode === "register" ? "注册并登录" : "登录")}
          </button>
          <button
            onClick={onClose}
            disabled={submitting}
            style={btnSecondary}
          >
            取消
          </button>
        </div>

        <div style={{ marginTop: 16, fontSize: 13, textAlign: "center" }}>
          {mode === "register" ? (
            <>
              已有账号？
              <a
                onClick={(e) => { e.preventDefault(); setMode("login"); }}
                href="#"
                style={{ color: "var(--accent, #2563eb)", marginLeft: 4 }}
              >
                登录
              </a>
            </>
          ) : (
            <>
              第一次使用？
              <a
                onClick={(e) => { e.preventDefault(); setMode("register"); }}
                href="#"
                style={{ color: "var(--accent, #2563eb)", marginLeft: 4 }}
              >
                注册
              </a>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  border: "1px solid var(--border, #ccc)",
  borderRadius: 4,
  background: "var(--bg-input, #fff)",
  color: "var(--fg, #000)",
  fontSize: 14,
};

const btnSecondary: React.CSSProperties = {
  padding: "10px 16px",
  background: "transparent",
  color: "var(--fg, #000)",
  border: "1px solid var(--border, #ccc)",
  borderRadius: 4,
  cursor: "pointer",
};
