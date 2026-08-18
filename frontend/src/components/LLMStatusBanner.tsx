/** LLMStatusBanner.tsx - 2026-08-18

用户在 WorldBuild / Outline / ThemeOpening 等需要 LLM 的页面**进入时**显示后端 LLM 状态。

设计动机（用户报告 #3 + #5 的架构修复）：
之前用户点「开始构建 / 生成大纲」之后才会失败，且失败信息不可操作（"调用 LLM 失败"）。
根因：前端没有任何方式知道后端 LLM 是否就绪。

修法：在每个需要 LLM 的页面顶部显示一个状态 banner：
- can_run_llm=true（mock 模式或 live 已配置）：
    折叠成一行 chip「✓ LLM 已就绪 · mock 模式」/「✓ LLM 已就绪 · deepseek · deepseek-chat」
- can_run_llm=false：
    醒目 banner「⚠ 当前 LLM 不可用」+ 失败原因 + 「去配置 →」按钮
    点「去配置」跳 /settings/providers（已有页面）
- loading / 探测失败：显示「正在检测后端…」「检测失败：后端没起来？点这里重试」

这样用户在**第一次进入页面**就知道能不能跑，不再点了之后才被告知失败。

CLAUDE.md 红线：banner 只展示 status 字段，不展示 LLM 原始响应里的密钥 / prompt。
*/

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ProviderHealth } from "../types";

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: ProviderHealth }
  | { kind: "down"; error: string }
  | { kind: "unavailable"; health: ProviderHealth };

interface Props {
  /** 用户进入页面时是否要展开（默认折叠）。 */
  defaultExpanded?: boolean;
  /** 隐藏 mock 模式提示（用于已经显式选择 mock 模式的页面）。 */
  hideWhenMock?: boolean;
}

export function LLMStatusBanner({ defaultExpanded = false, hideWhenMock = false }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [expanded, setExpanded] = useState(defaultExpanded);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    function load() {
      setState({ kind: "loading" });
      api.getProviderHealth()
        .then((h) => {
          if (cancelled) return;
          if (h.can_run_llm) setState({ kind: "ok", health: h });
          else setState({ kind: "unavailable", health: h });
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setState({ kind: "down", error: String(e) });
        });
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // mock 模式且 hideWhenMock=true → 完全不渲染（页面自有 mock 引导）
  if (state.kind === "ok" && state.health.mode === "mock" && hideWhenMock) return null;

  if (state.kind === "loading") {
    return (
      <div
        className="llm-banner llm-banner--loading"
        role="status"
        aria-live="polite"
        style={{
          padding: "8px 14px",
          margin: "0 0 16px",
          borderRadius: 8,
          border: "1px dashed var(--border-2)",
          color: "var(--text-muted, #9098B0)",
          fontSize: 12.5,
        }}
      >
        正在检测后端 LLM 状态…
      </div>
    );
  }

  if (state.kind === "down") {
    return (
      <div
        className="llm-banner llm-banner--down"
        role="alert"
        style={{
          padding: "12px 14px",
          margin: "0 0 16px",
          borderRadius: 8,
          background: "var(--stamp-soft, rgba(224,108,95,0.12))",
          border: "1px solid var(--stamp-border, rgba(224,108,95,0.40))",
          color: "var(--stamp, #E06C5F)",
          fontSize: 13,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 4 }}>
          ⚠ 后端未响应 — 无法生成内容
        </div>
        <div style={{ opacity: 0.85, fontSize: 12 }}>
          {state.error}（默认地址 <code style={{ fontFamily: "var(--font-mono)" }}>http://127.0.0.1:8132</code>）
        </div>
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => window.location.reload()}
            aria-label="重试检测"
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  // state.kind === "ok" | "unavailable"
  const h = state.kind === "ok" ? state.health : state.health;
  const isMock = h.mode === "mock";
  const isOk = state.kind === "ok";
  const providerLabel = isMock ? "mock 模式" : `${h.active_provider ?? "?"} · ${h.active_model ?? "?"}`;

  if (isOk && !expanded) {
    // 折叠态：一行 chip + 展开按钮
    return (
      <div
        className="llm-banner llm-banner--ok-collapsed"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 12px",
          margin: "0 0 14px",
          borderRadius: 6,
          background: "var(--moss-soft, rgba(111,188,138,0.14))",
          border: "1px solid var(--moss-border, rgba(111,188,138,0.40))",
          color: "var(--moss, #6FBC8A)",
          fontSize: 12,
        }}
      >
        <span>✓ LLM 已就绪</span>
        <span style={{ opacity: 0.7 }}>·</span>
        <span style={{ fontFamily: "var(--font-mono)" }}>{providerLabel}</span>
        <div style={{ flex: 1 }} />
        <button
          type="button"
          onClick={() => setExpanded(true)}
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "inherit",
            opacity: 0.75,
            fontSize: 11.5,
          }}
          aria-label="展开 LLM 状态详情"
        >
          ▾ 详情
        </button>
      </div>
    );
  }

  // unavailable 或 ok + expanded：详细状态
  return (
    <div
      className={`llm-banner ${isOk ? "llm-banner--ok" : "llm-banner--unavailable"}`}
      role={isOk ? "status" : "alert"}
      style={{
        padding: "12px 14px",
        margin: "0 0 16px",
        borderRadius: 8,
        background: isOk ? "var(--moss-soft, rgba(111,188,138,0.14))" : "var(--stamp-soft, rgba(224,108,95,0.12))",
        border: `1px solid ${isOk ? "var(--moss-border, rgba(111,188,138,0.40))" : "var(--stamp-border, rgba(224,108,95,0.40))"}`,
        color: isOk ? "var(--moss, #6FBC8A)" : "var(--stamp, #E06C5F)",
        fontSize: 13,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <strong>{isOk ? "✓ LLM 已就绪" : "⚠ LLM 不可用 — 生成操作会失败"}</strong>
        <span style={{ opacity: 0.7 }}>·</span>
        <span style={{ fontFamily: "var(--font-mono)" }}>{providerLabel}</span>
        {isOk && (
          <button
            type="button"
            onClick={() => setExpanded(false)}
            style={{
              marginLeft: "auto",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "inherit",
              opacity: 0.75,
              fontSize: 11.5,
            }}
            aria-label="折叠"
          >
            ▴ 收起
          </button>
        )}
      </div>

      <div style={{ opacity: 0.9, fontSize: 12.5, marginBottom: 8 }}>{h.message}</div>

      {/* 角色路由明细（即使 ok 也展示，让用户能确认角色路由正确） */}
      <details style={{ marginTop: 4 }}>
        <summary style={{ cursor: "pointer", fontSize: 11.5, opacity: 0.85 }}>
          角色路由（3 个生成角色的 provider 映射）
        </summary>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 11.5, opacity: 0.85 }}>
          {Object.entries(h.roles).map(([role, st]) => (
            <li key={role}>
              <code style={{ fontFamily: "var(--font-mono)" }}>{role}</code>
              {" → "}
              <span style={{ color: st.ok ? "var(--moss)" : "var(--stamp)" }}>
                {st.provider}{st.model ? ` · ${st.model}` : ""}
              </span>
              {st.reason && <span style={{ opacity: 0.6 }}> — {st.reason}</span>}
            </li>
          ))}
        </ul>
      </details>

      {!isOk && (
        <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => navigate("/settings/providers")}
            aria-label="去配置供应商"
          >
            去配置供应商 →
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => window.location.reload()}
            aria-label="重新检测"
          >
            重新检测
          </button>
        </div>
      )}
    </div>
  );
}