import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Provider, RoleAssignment } from "../types";

type Drafts = Record<string, { provider_id: string; model_override: string }>;

export default function RoleAssignments() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [roles, setRoles] = useState<RoleAssignment[]>([]);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function refresh() {
    setLoading(true);
    setLoadError(null);
    try {
      const [providerRows, roleRows] = await Promise.all([api.listProviders(), api.listRoleAssignments()]);
      if (!mountedRef.current) return;
      setProviders(providerRows);
      setRoles(roleRows);
      setDrafts(
        Object.fromEntries(
          roleRows.map((role) => [
            role.role_key,
            {
              provider_id: role.provider_id || "",
              model_override: role.model_override || "",
            },
          ]),
        ),
      );
    } catch (e) {
      if (!mountedRef.current) return;
      setLoadError(String(e));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function updateDraft(roleKey: string, patch: Partial<Drafts[string]>) {
    setDrafts((prev) => ({ ...prev, [roleKey]: { ...prev[roleKey], ...patch } }));
  }

  async function saveRole(role: RoleAssignment) {
    const draft = drafts[role.role_key];
    if (!draft) return;
    setSavingKey(role.role_key);
    setError(null);
    setMessage(null);
    try {
      await api.updateRoleAssignment(role.role_key, {
        provider_id: draft.provider_id || null,
        model_override: draft.model_override.trim() || null,
      });
      if (!mountedRef.current) return;
      setMessage(`已保存：${role.label}`);
      await refresh();
    } catch (e) {
      if (!mountedRef.current) return;
      setError(String(e));
    } finally {
      if (mountedRef.current) setSavingKey(null);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">角色绑定</h1>
          <div className="page-header__sub">
            15 个写作角色 · 把每个角色指给一个 Provider + 模型，下次写作时即时生效
          </div>
        </div>
        <div className="page-header__actions">
          <span className="badge-soft badge">共 {roles.length || 15} 个角色</span>
        </div>
      </div>

      {error && <div className="banner banner-danger">{error}</div>}
      {message && <div className="banner banner-success">{message}</div>}

      {loadError && !loading && (
        <div className="banner banner-danger" role="alert">
          <span>加载失败：{loadError}</span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => refresh()}
            disabled={loading}
            aria-label="重试加载角色绑定"
          >
            重试
          </button>
        </div>
      )}

      <div className="card">
        <h3 className="card__title">模型路由</h3>
        {loading && <p className="loading-text">加载中…</p>}
        {!loading && roles.length === 0 && <div className="empty-state">角色注册表还没有初始化。</div>}
        {!loading && roles.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>角色</th>
                  <th>Provider</th>
                  <th>模型覆盖</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {roles.map((role) => {
                  const draft = drafts[role.role_key] || { provider_id: "", model_override: "" };
                  return (
                    <tr key={role.role_key}>
                      <td>
                        <strong>{role.label}</strong>
                        <div className="mono">{role.role_key}</div>
                      </td>
                      <td>
                        <select
                          value={draft.provider_id}
                          onChange={(e) => updateDraft(role.role_key, { provider_id: e.target.value })}
                        >
                          <option value="">不绑定</option>
                          {providers.map((provider) => (
                            <option key={provider.id} value={provider.id}>
                              {provider.name} ({provider.provider_type})
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          value={draft.model_override}
                          onChange={(e) => updateDraft(role.role_key, { model_override: e.target.value })}
                          placeholder="留空使用 Provider 默认模型"
                        />
                      </td>
                      <td className="table-actions">
                        <button
                          type="button"
                          className="btn"
                          onClick={() => saveRole(role)}
                          disabled={savingKey === role.role_key}
                          aria-label={`保存角色 ${role.label}`}
                        >
                          {savingKey === role.role_key ? "保存中…" : "保存"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
