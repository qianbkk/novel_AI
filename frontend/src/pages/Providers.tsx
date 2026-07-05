import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Provider, ProviderForm, ProviderCreate } from "../types";

const PROVIDER_TYPES: Provider["provider_type"][] = ["anthropic", "deepseek", "gemini", "kimi", "minimax", "custom"];

const EMPTY_FORM: ProviderForm = {
  name: "",
  provider_type: "deepseek",
  api_base: "",
  api_key: "",
  default_model: "",
  extra_json: null,
  needs_proxy: false,
};

// 迭代 #83：用户审计反馈前端没有任何关于 master key 失效风险的提示。
// 在 Providers 页面顶部加醒目 banner 说明 dev 模式下 master key 持久化
// 行为 + 如何显式设固定 MASTER_KEY 保护已有数据。
// （后端 #82 已经修了 dev mode 自动持久化到 data/.dev_master_key 文件，
//  让 --reload 重启不会失效——但用户仍应了解行为）
const MASTER_KEY_DEV_WARNING = (
  <div
    role="alert"
    style={{
      background: "#fff8e1",
      border: "1px solid #ffcc02",
      borderRadius: 6,
      padding: "10px 14px",
      marginBottom: 16,
      fontSize: 13,
      lineHeight: 1.5,
      color: "#5d4506",
    }}
  >
    <strong style={{ display: "block", marginBottom: 4 }}>
      ⚠️ 关于 Provider Key 加密
    </strong>
    后端默认用 dev mode 持久化的 master key 加密你填写的 API key——
    <code>backend/data/.dev_master_key</code> (gitignored)。每次保存代码 uvicorn
    重启都会读回同一个 key，<strong>已配置 Key 不会失效</strong>。
    <br />
    但如果你清掉 <code>backend/data/</code> 目录或换了台电脑，
    <strong>所有 Provider Key 会永久失效</strong>（"解密失败"）。
    想要真正固定：<code>export MASTER_KEY=&lt;base64&gt;</code>{" "}
    <span style={{ color: "#666" }}>
      （生成：<code>python -m scripts.generate_master_key --print</code>）
    </span>
  </div>
);

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
    // 后端不返回明文 api_key：列表里 provider.api_key 是 undefined。
    // 编辑表单如果用户没改 api_key，提交时仍要发原值；这里用空字符串占位，
    // handleSubmit 会在 editingId 模式下提示用户重新填 api_key。
    setForm({
      name: provider.name,
      provider_type: provider.provider_type,
      api_base: provider.api_base || "",
      api_key: "",  // 不从列表预填（后端不返回明文）
      default_model: provider.default_model || "",
      extra_json: provider.extra_json || null,
      needs_proxy: provider.needs_proxy,
    });
  }

  async function handleSubmit() {
    if (!form.name.trim()) return;
    // 编辑模式下 api_key 是空 → 阻止提交（避免误清空）
    if (editingId && !form.api_key.trim()) {
      setError("编辑 Provider 时必须重新填写 api_key（后端不返回明文，无法预填）");
      return;
    }
    setSaving(true);
    setError(null);
    // 表单值 → API 请求体：把空字符串归一为 null（api_base / default_model）
    const payload: ProviderCreate = {
      name: form.name.trim(),
      provider_type: form.provider_type,
      api_base: form.api_base.trim() || null,
      // 编辑时 api_key 已在第 71 行校验过必填；新增时 form.api_key 是空 → 用户
      // 必须自己填（前端不静默放过）。后端 save 时 api_key 空字符串 = "没有设置 key"，
      // 不会清空已有 key（这是后端 update_provider 的设计：空字符串跳过更新）。
      api_key: form.api_key.trim(),
      default_model: form.default_model.trim() || null,
      extra_json: form.extra_json,
      needs_proxy: form.needs_proxy,
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
      {/* 迭代 #83：顶部 master key 加密行为警告 — 让用户知道 dev mode 安全但需了解 */}
      {MASTER_KEY_DEV_WARNING}

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
          {form.needs_proxy && (
            <div className="banner banner-info" style={{ fontSize: 12, padding: "8px 12px", marginTop: 4 }}>
              请在 backend 进程的 <span className="text-mono">{form.provider_type.toUpperCase()}_PROXY</span> 环境变量里填代理 URL（如 <span className="text-mono">http://127.0.0.1:7890</span>），后端 LLM 路由会自动读取。
            </div>
          )}
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
