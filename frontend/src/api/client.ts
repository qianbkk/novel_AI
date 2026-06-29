import type {
  Project,
  JobStatus,
  WorldBuildResult,
  ChapterListItem,
  ChapterCreateResult,
  ChapterSearchResult,
  Provider,
  RoleAssignment,
  BridgeRun,
  BridgeStatus,
  BridgePendingItem,
  NovelAIBinding,
} from "../types";

// 后端地址：默认本机 8123 端口，部署到别的地方时改 frontend/.env 里的 VITE_API_BASE 即可
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8123";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`请求失败 ${resp.status}: ${path} ${text}`);
  }
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export const api = {
  listProjects: () => request<Project[]>("/projects"),

  getProject: (id: string) => request<Project>(`/projects/${id}`),

  createProject: (payload: {
    title?: string;
    genre: string;
    audience?: string;
    config_json: Record<string, unknown>;
  }) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  startWorldbuild: (projectId: string) =>
    request<JobStatus>(`/projects/${projectId}/worldbuild/start`, { method: "POST" }),

  getWorldbuildResult: (projectId: string) =>
    request<WorldBuildResult>(`/projects/${projectId}/worldbuild/result`),

  /** SSE 流不走 fetch，单独提供一个建好的 EventSource，调用方自己 addEventListener */
  worldbuildStreamUrl: (projectId: string, jobId: string) =>
    `${API_BASE}/projects/${projectId}/worldbuild/stream?job_id=${jobId}`,

  listChapters: (projectId: string) => request<ChapterListItem[]>(`/projects/${projectId}/chapters`),

  createChapter: (projectId: string, payload: { chapter_no: number; title?: string; content: string }) =>
    request<ChapterCreateResult>(`/projects/${projectId}/chapters`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  searchChapters: (projectId: string, query: string, characterId?: string) => {
    const params = new URLSearchParams({ query, top_k: "5" });
    if (characterId) params.set("character_id", characterId);
    return request<ChapterSearchResult[]>(`/projects/${projectId}/chapters/search?${params.toString()}`);
  },

  listProviders: () => request<Provider[]>("/providers"),

  createProvider: (payload: Omit<Provider, "id">) =>
    request<Provider>("/providers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateProvider: (id: string, payload: Omit<Provider, "id">) =>
    request<Provider>(`/providers/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  deleteProvider: (id: string) =>
    request<{ ok: boolean }>(`/providers/${id}`, {
      method: "DELETE",
    }),

  listRoleAssignments: () => request<RoleAssignment[]>("/role-assignments"),

  updateRoleAssignment: (roleKey: string, payload: { provider_id: string | null; model_override: string | null }) =>
    request<RoleAssignment>(`/role-assignments/${roleKey}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  triggerBridge: (projectId: string, command: string, args: string[] = []) =>
    request<BridgeRun>(`/projects/${projectId}/bridge/run`, {
      method: "POST",
      body: JSON.stringify({ command, args }),
    }),

  bridgeStreamUrl: (projectId: string, bridgeRunId: string) => {
    const params = new URLSearchParams({ run_id: bridgeRunId });
    return `${API_BASE}/projects/${projectId}/bridge/stream?${params.toString()}`;
  },

  getNovelAIBinding: (projectId: string) =>
    request<NovelAIBinding>(`/projects/${projectId}/bridge/binding`),

  updateNovelAIBinding: (projectId: string, payload: { novel_ai_dir: string; novel_id?: string | null }) =>
    request<NovelAIBinding>(`/projects/${projectId}/bridge/binding`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  pushConcept: (projectId: string) =>
    request<Record<string, unknown>>(`/projects/${projectId}/bridge/push-concept`, { method: "POST" }),

  pullSetting: (projectId: string) =>
    request<Record<string, unknown>>(`/projects/${projectId}/bridge/pull-setting`, { method: "POST" }),

  importChapters: (projectId: string) =>
    request<Record<string, unknown>[]>(`/projects/${projectId}/bridge/import-chapters`, { method: "POST" }),

  getBridgeStatus: (projectId: string) => request<BridgeStatus>(`/projects/${projectId}/bridge/status`),

  getBridgePending: (projectId: string) => request<BridgePendingItem[]>(`/projects/${projectId}/bridge/pending`),

  getBridgeBudget: (projectId: string) =>
    request<Record<string, unknown>[]>(`/projects/${projectId}/bridge/budget`),

  submitReview: (
    projectId: string,
    payload: { task_id: string; action: "accept" | "reject" | "edit"; edited_content?: string },
  ) =>
    request<Record<string, unknown>>(`/projects/${projectId}/bridge/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
