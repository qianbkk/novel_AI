import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Provider } from "../types";

const PROVIDER_TYPES: Provider["provider_type"][] = ["anthropic", "deepseek", "gemini", "kimi", "minimax", "custom"];

type ProviderForm = Omit<Provider, "id">;

const EMPTY_FORM: ProviderForm = {
  name: "",
  provider_type: "deepseek",
  api_base: "",
  api_key: "",
  default_model: "",
  extra_json: null,
  needs_proxy: false,
};

export default function Providers() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [form, setForm] = useState<ProviderForm>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editingProvider = useMemo(
    () => providers.find((provider) => provider.id === editingId) || null,
    [editingId, providers],
  );

  async function refresh() {
    setLoading(true);
    try {
      setProviders(await api.listProviders());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function resetForm() {
    setEditingId(null);
    setForm(EMPTY_FORM);
  }

  function startEdit(provider: Provider) {
    setEditingId(provider.id);
    setForm({
      name: provider.name,
      provider_type: provider.provider_type,
      api_base: provider.api_base || "",
      api_key: provider.api_key || "",
      default_model: provider.default_model || "",
      extra_json: provider.extra_json || null,
      needs_proxy: provider.needs_proxy,
    });
  }

  async function handleSubmit() {
    if (!form.name.trim()) return;
    setSaving(true);
    setError(null);
    const payload: ProviderForm = {
      ...form,
      name: form.name.trim(),
      api_base: form.api_base?.trim() || null,
      api_key: form.api_key?.trim() || null,
      default_model: form.default_model?.trim() || null,
    };

    try {
      if (editingId) {
        await api.updateProvider(editingId, payload);
      } else {
        await api.createProvider(payload);
      }
      resetForm();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(provider: Provider) {
    if (!window.confirm(`确定删除 Provider「${provider.name}」？相关角色绑定会被清空。`)) return;
    setError(null);
    try {
      await api.deleteProvider(provider.id);
      if (editingId === provider.id) resetForm();
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-header__title">模型供应商</h1>
          <div className="page-header__sub">
            6 种供应商类型 · API Key 仅写入本地 SQLite · 删除会级联清空角色绑定
          </div>
        </div>
        {editingProvider && (
          <div className="page-header__actions">
            <span className="badge-soft badge">正在编辑：{editingProvider.name}</span>
          </div>
        )}
      </div>

      {error && <div className="banner banner-danger">{error}</div>}

      <div className="card">
        <h3 className="card__title">{editingId ? "编辑 Provider" : "新增 Provider"}</h3>
        <div className="form-grid">
          <div className="field">
            <label>名称</label>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="field">
            <label>类型</label>
            <select
              value={form.provider_type}
              onChange={(e) => setForm({ ...form, provider_type: e.target.value as Provider["provider_type"] })}
            >
              {PROVIDER_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>API Base</label>
            <input value={form.api_base || ""} onChange={(e) => setForm({ ...form, api_base: e.target.value })} />
          </div>
          <div className="field">
            <label>API Key</label>
            <input
              type="password"
              value={form.api_key || ""}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            />
          </div>
          <div className="field">
            <label>默认模型</label>
            <input
              value={form.default_model || ""}
              onChange={(e) => setForm({ ...form, default_model: e.target.value })}
            />
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={form.needs_proxy}
              onChange={(e) => setForm({ ...form, needs_proxy: e.target.checked })}
            />
            需要代理
          </label>
        </div>
        <div className="button-row">
          <button className="btn btn-primary" onClick={handleSubmit} disabled={saving || !form.name.trim()}>
            {saving ? "保存中…" : editingId ? "保存修改" : "新增 Provider"}
          </button>
          {editingId && (
            <button className="btn" onClick={resetForm} disabled={saving}>
              取消编辑
            </button>
          )}
        </div>
      </div>

      <div className="card mt-24">
        <h3 className="card__title">已配置 Provider</h3>
        {loading && <p className="loading-text">加载中…</p>}
        {!loading && providers.length === 0 && <div className="empty-state">还没有 Provider。</div>}
        {providers.map((provider) => (
          <div className="entity-card provider-row" key={provider.id}>
            <div>
              <span className="entity-card__name">{provider.name}</span>
              <span className="entity-card__meta">
                {provider.provider_type} · {provider.default_model || "未设置默认模型"}
              </span>
              <div className="entity-card__desc mono">{provider.api_base || "未设置 API Base"}</div>
            </div>
            <div className="button-row">
              {provider.needs_proxy && <span className="badge-soft badge">代理</span>}
              <button className="btn" onClick={() => startEdit(provider)}>
                编辑
              </button>
              <button className="btn btn-danger" onClick={() => handleDelete(provider)}>
                删除
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
