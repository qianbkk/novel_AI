import type {
  Project,
  JobStatus,
  WorldBuildResult,
  ChapterListItem,
  ChapterFull,
  ChapterCharacter,
  ChapterCreateResult,
  ChapterSearchResult,
  Provider,
  ProviderCreate,
  RoleAssignment,
  BridgeRun,
  BridgeStatus,
  BridgePendingItem,
  BridgeBudget,
  NovelAIBinding,
  RuleConfig,
  PostProcessResult,
  ForeshadowingRow,
  AiAssistLevel,
  // Phase 4 新增
  CharacterSummary,
  CharacterCardOut,
  CharacterRelation,
  RelationGraph,
  WorldviewRichOut,
} from "../types";

// 后端地址：默认 8132（开发用），部署时改 frontend/.env 里的 VITE_API_BASE
// 注：本地开发后端通常跑在 8132 端口，因为 8123 经常被测试残留进程占着，
// 强抢会失败。统一走 8132 避免「前端 404、后端没起来」这种误判。
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8132";

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
  // 包一层 JSON 解析错误：原始 SyntaxError 容易让用户以为是代码 bug。
  // 现在 catch 后吐 "响应不是有效 JSON: ${path} body[:200]=..." 让用户
  // 知道是后端返回了非 JSON（比如 HTML 错误页 / 半写文件 / proxy 拦截）。
  try {
    return JSON.parse(text) as T;
  } catch (e) {
    const snippet = text.slice(0, 200).replace(/\s+/g, " ");
    throw new Error(
      `响应不是有效 JSON (${path}): ${(e as Error).message} | body[:200]=${snippet}`,
    );
  }
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

  getChapter: (projectId: string, chapterId: string) =>
    request<ChapterFull>(`/projects/${projectId}/chapters/${chapterId}`),

  getChapterCharacters: (projectId: string, chapterId: string) =>
    request<ChapterCharacter[]>(`/projects/${projectId}/chapters/${chapterId}/characters`),

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

  createProvider: (payload: ProviderCreate) =>
    request<Provider>("/providers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateProvider: (id: string, payload: ProviderCreate) =>
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

  triggerBridge: (projectId: string, command: string, args: string[] = [], outlineMode?: string) =>
    request<BridgeRun>(`/projects/${projectId}/bridge/run`, {
      method: "POST",
      body: JSON.stringify({ command, args, outline_mode: outlineMode }),
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
    request<BridgeBudget>(`/projects/${projectId}/bridge/budget`),

  submitReview: (
    projectId: string,
    payload: { task_id: string; action: "accept" | "reject" | "edit"; content?: string },
  ) =>
    request<Record<string, unknown>>(`/projects/${projectId}/bridge/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // ─── 规则中心 ───
  getRules: (projectId: string) =>
    request<RuleConfig>(`/projects/${projectId}/rules`),

  putRules: (projectId: string, payload: {
    style?: "webnovel" | "literary" | "wuxia";
    taboos?: string[];
    template?: string;
    extra?: Record<string, unknown>;
  }) =>
    request<RuleConfig>(`/projects/${projectId}/rules`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  postProcess: (projectId: string, payload: {
    tool: "logic" | "venom" | "deai";
    chapter_no?: number;
    style?: string;
    taboos?: string[];
  }) =>
    request<PostProcessResult>(`/projects/${projectId}/rules/post-process`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // ─── 伏笔状态流转 ───
  listForeshadowings: (projectId: string) =>
    request<ForeshadowingRow[]>(`/projects/${projectId}/foreshadowings`),

  updateForeshadowingStatus: (projectId: string, foreshadowingId: string, status: string) =>
    request<ForeshadowingRow>(`/projects/${projectId}/foreshadowings/${foreshadowingId}/status`, {
      method: "PUT",
      body: JSON.stringify({ status }),
    }),

  // ─── AI 参与度声明 ───
  getAiAssistLevel: (projectId: string) =>
    request<AiAssistLevel>(`/projects/${projectId}/ai-assist-level`),

  putAiAssistLevel: (projectId: string, level: "ai_assisted" | "human_primary" | "unset") =>
    request<AiAssistLevel>(`/projects/${projectId}/ai-assist-level`, {
      method: "PUT",
      body: JSON.stringify({ ai_assist_level: level }),
    }),

  // ─── Phase 4：世界构建板块 5 个新 endpoint ───
  getWorldviewRich: (projectId: string) =>
    request<WorldviewRichOut>(`/projects/${projectId}/worldview/rich`),

  listCharacters: (projectId: string) =>
    request<CharacterSummary[]>(`/projects/${projectId}/characters`),

  getCharacterCard: (projectId: string, characterId: string) =>
    request<CharacterCardOut>(`/projects/${projectId}/characters/${characterId}`),

  getCharacterRelations: (projectId: string, characterId: string) =>
    request<CharacterRelation[]>(`/projects/${projectId}/characters/${characterId}/relations`),

  getRelationsGraph: (projectId: string) =>
    request<RelationGraph>(`/projects/${projectId}/relations/graph`),
};