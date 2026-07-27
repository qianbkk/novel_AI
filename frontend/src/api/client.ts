import type {
  Project,
  JobStatus,
  WorldBuildResult,
  StageListOut,
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
  BridgeMemory,
  NovelAIBinding,
  RuleConfig,
  PostProcessResult,
  ForeshadowingRow,
  AiAssistLevel,
  // 弧级大纲
  OutlineOut,
  OutlineCreatePayload,
  ArcGeneratePayload,
  // 角色卡 / 世界观 / 关系图（前端持久类型）
  CharacterSummary,
  CharacterCardOut,
  CharacterRelation,
  RelationGraph,
  WorldviewRichOut,
  // 多用户认证
  User,
  TokenResponse,
} from "../types";

// 后端地址：默认 8132（开发用），部署时改 frontend/.env 里的 VITE_API_BASE
// 注：本地开发后端通常跑在 8132 端口，因为 8123 经常被测试残留进程占着，
// 强抢会失败。统一走 8132 避免「前端 404、后端没起来」这种误判。
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8132";

// ─── JWT token 管理 ───
// 审计 #10 (2026-07-20)：token 存 localStorage（不是 cookie）。
// 后端 login/register 同时下发 HttpOnly cookie，但当前后端只解
// Authorization 头（见 backend/app/auth.py:_extract_bearer），所以
// 真正生效的鉴权来源是这里 setItem 的值。Cookie 是"未来 cookie-only
// 切换"预留通道——切换涉及 CSRF 防护 / 反代 HTTPS 强制 / 多 worker
// 同步，超出最小实现边界。XSS 防护前提：当前前端无 dangerouslySetInnerHTML。
const TOKEN_KEY = "novel_ai_jwt";

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // localStorage 不可用（隐私模式 / SSR）→ 退化为内存态
    // 不抛错：组件层 401 仍能提示用户登录
  }
}

/** 触发自定义事件，UI 可监听这个决定是否弹出登录对话框。 */
export function dispatchAuthRequired(): void {
  try {
    window.dispatchEvent(new CustomEvent("novel_ai:auth_required"));
  } catch {
    // ignore
  }
}

// ─── 2026-07-25 修复（API client 缺 abort/timeout/retry）───
// 之前 request() 直接 await fetch(..., options)，没有 AbortSignal / 超时 / 重试。
// 三个真实风险：
//   1) 用户切走 30 秒后请求还在跑 — 内存 + 钱 + token 都在浪费
//   2) fetch 默认 0 永等 — 网络断开页面永远 loading
//   3) HTTP 529 (MiniMax 服务器过载) / 网抖 — 应该自动退避重试 1-2 次
// 修法：加 RequestOptions { signal?, timeoutMs?, retries? }；默认 timeout 30s + retries 1。
//   AbortSignal 透传给 fetch；超时 + 重试都用内部 AbortController 实现。
export interface RequestOptions extends Omit<RequestInit, "signal"> {
  /** 外部 AbortSignal — 用于 useEffect cleanup 取消。timeout 内部会创建自己的 controller。 */
  signal?: AbortSignal;
  /** 默认 30_000ms（写操作 60_000ms）。设为 0 表示禁用超时（用于 SSE 长连接 / 文件上传）。 */
  timeoutMs?: number;
  /** 默认 1（GET） / 0（POST 写操作）。GET 请求 5xx / 网络错误会重试 1 次（间隔 800ms）。
   *  POST 默认不重试（写操作幂等性不可假设）。调用方显式传 retries: N 覆盖。 */
  retries?: number;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_RETRIES = 1;
const RETRY_BACKOFF_MS = 800;

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function isRetryable(err: unknown): boolean {
  // AbortError 是用户主动取消，不重试
  if (isAbortError(err)) return false;
  if (!(err instanceof Error)) return false;
  // 网络/超时/5xx 都重试
  if (err.message.startsWith("服务器错误 5")) return true;
  if (err.message.includes("Failed to fetch") ||
      err.message.includes("NetworkError") ||
      err.message.includes("Timeout") ||
      err.message.includes("timeout")) return true;
  return false;
}

function withTimeout(parentSignal: AbortSignal | undefined, ms: number): {
  signal: AbortSignal;
  cancel: () => void;
} {
  const ctrl = new AbortController();
  const onParentAbort = () => ctrl.abort(parentSignal?.reason);
  if (parentSignal) {
    if (parentSignal.aborted) ctrl.abort(parentSignal.reason);
    else parentSignal.addEventListener("abort", onParentAbort, { once: true });
  }
  const timer = setTimeout(() => ctrl.abort(new DOMException("Request timeout", "TimeoutError")), ms);
  const cancel = (): void => {
    clearTimeout(timer);
    if (parentSignal) parentSignal.removeEventListener("abort", onParentAbort);
  };
  return { signal: ctrl.signal, cancel };
}

async function request<T>(path: string, options?: RequestOptions): Promise<T> {
  const method = (options?.method || "GET").toUpperCase();
  const isWrite = method !== "GET" && method !== "HEAD";
  const timeoutMs = options?.timeoutMs ?? (isWrite ? 60_000 : DEFAULT_TIMEOUT_MS);
  // 默认按 method 决定：GET 1 次重试（5xx / 网络错），POST/PUT/DELETE 0 次（写操作不重试）。
  // 调用方可显式传 retries: N 覆盖。
  const maxRetries = options?.retries ?? (isWrite ? 0 : DEFAULT_RETRIES);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options?.headers as Record<string, string>) || {}),
  };
  // 有 token 就带 Authorization（dev 模式无 token 也允许）
  const token = getStoredToken();
  if (token && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let lastError: unknown = null;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const { signal, cancel } = timeoutMs > 0
      ? withTimeout(options?.signal, timeoutMs)
      : { signal: options?.signal as AbortSignal | undefined, cancel: () => {} };
    try {
      if (!signal) {
        // 没有 timeout + 没有外部 signal — 直接 fetch
        const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });
        return await handleResponse<T>(resp, path);
      }
      const resp = await fetch(`${API_BASE}${path}`, { ...options, headers, signal });
      cancel();
      return await handleResponse<T>(resp, path);
    } catch (err) {
      cancel();
      // 用户主动取消（含外部 signal 触发）— 不重试，直接抛
      if (isAbortError(err)) throw err;
      lastError = err;
      // 不可重试 / 已用完次数
      if (!isRetryable(err) || attempt >= maxRetries) throw err;
      // 退避后重试
      await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS * (attempt + 1)));
    }
  }
  // 不可达：循环已经 throw；这里只是让 TS 满足返回类型
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

async function handleResponse<T>(resp: Response, path: string): Promise<T> {
  if (resp.status === 401) {
    // 生产模式下后端会拒绝无 token 请求；dev 模式实际不会触发。
    // 仅清 token（不立即抛）→ 让上层路由用 catch 处理。
    setStoredToken(null);
    dispatchAuthRequired();
  }

  if (!resp.ok) {
    // 审计 #4 (2026-07-20)：5xx 错误体可能含 SQL/堆栈/内部路径，
    // 直接拼到 Error message 会经 toast / setLoadError 展示给用户。
    // 4xx 通常是业务错误（detail 是用户可读信息）→ 保留 body 透传。
    if (resp.status >= 500 && resp.status < 600) {
      throw new Error(`服务器错误 ${resp.status} (${path})`);
    }
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
  listProjects: (params?: { q?: string; genre?: string }) => {
    const search = params ? new URLSearchParams(
      Object.entries(params).filter(([_, v]) => v != null && v !== "") as [string, string][]
    ).toString() : "";
    return request<Project[]>(`/projects${search ? `?${search}` : ""}`);
  },

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

  /** 10 阶段清单：避免前端硬编码跟后端 STAGES 漂移 */
  listWorldbuildStages: () => request<StageListOut>("/worldbuild/stages"),

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

  // ─── 重新导入 chapters（修正旧标题从内容首句派生） ───
  reimportChapters: (projectId: string) =>
    request<Array<{ chapter_no: number; title: string; mode: string }>>(
      `/projects/${projectId}/bridge/reimport-chapters`,
      { method: "POST" }
    ),

  // ─── LLM 生成章节标题 ───
  regenerateTitles: (projectId: string, payload: {
    limit?: number;
    only_missing?: boolean;
    chapter_nos?: number[];
    sample?: boolean;
  }) =>
    request<{
      processed: number;
      updated: number;
      total_cost_usd: number;
      changes: Array<{ chapter_no: number; old_title: string | null; new_title: string; cost: number }>;
      sample: boolean;
    }>(`/projects/${projectId}/regenerate-titles`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

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

  /** 切换 audit_mode（草稿 / 完整 / 精简）。下次 run 启动时通过 env 传给 subprocess。 */
  setAuditMode: (projectId: string, mode: "full" | "lite" | "draft") =>
    request<{ mode: string }>(`/projects/${projectId}/bridge/set-audit-mode`, {
      method: "POST",
      body: JSON.stringify({ mode }),
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

  // 分层记忆快照（L2 热/冷/约束 + L5 弧归档）。伏笔逾期 / 未过质量门的章节 /
  // tracker 解析失败都从这里读 —— 长篇跑飞前唯一能提前看到的信号。
  getBridgeMemory: (projectId: string) =>
    request<BridgeMemory>(`/projects/${projectId}/bridge/memory`),

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

  // ─── 世界构建板块 5 个 endpoint ───
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

  // ─── 弧级大纲管理 ───
  listOutlines: (projectId: string) =>
    request<OutlineOut[]>(`/projects/${projectId}/outlines`),

  createOutline: (projectId: string, payload: OutlineCreatePayload) =>
    request<OutlineOut>(`/projects/${projectId}/outlines`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateOutline: (projectId: string, outlineId: string, payload: Partial<OutlineCreatePayload> & { status?: string }) =>
    request<OutlineOut>(`/projects/${projectId}/outlines/${outlineId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteOutline: (projectId: string, outlineId: string) =>
    request<{ ok: boolean; deleted_id: string }>(`/projects/${projectId}/outlines/${outlineId}`, {
      method: "DELETE",
    }),

  generateOutline: (projectId: string, payload: ArcGeneratePayload) =>
    request<OutlineOut>(`/projects/${projectId}/outlines/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // ─── 多用户认证 ───
  // dev 模式（默认）：上面所有端点在没有 token 时仍可访问（兼容旧用户）
  // production 模式（NOVEL_PRODUCTION=1）：必须先 register / login 才能用
  register: async (payload: {
    email: string;
    password: string;
    display_name?: string | null;
  }): Promise<TokenResponse> => {
    const data = await request<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setStoredToken(data.access_token);
    return data;
  },

  login: async (payload: { email: string; password: string }): Promise<TokenResponse> => {
    const data = await request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setStoredToken(data.access_token);
    return data;
  },

  logout: (): void => {
    setStoredToken(null);
  },

  /** 带 timeout 守卫的 /auth/me：当后端 fail-fast / 401 时返回 null 而非抛。 */
  meOrNull: async (): Promise<User | null> => {
    const token = getStoredToken();
    if (!token) return null;
    try {
      return await request<User>("/auth/me");
    } catch {
      setStoredToken(null);
      return null;
    }
  },

  changePassword: (payload: { old_password: string; new_password: string }) =>
    request<{ ok: boolean }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

/**
 * withConcurrency — 限制 N 个 promise 并发跑。
 * 2026-07-25 修（Dashboard 4 并发模板化）：之前 Dashboard.tsx 自己手写
 * for + chunk + Promise.all；现在抽到 api 层共用，调用方只传并发上限。
 * 失败的任务不会中断其他任务，调用方可在 result 元组里看到 err。
 *
 * 用法：
 *   const [ok1, err1, ok2, err2] = await api.withConcurrency(2,
 *     () => fetchData(1),
 *     () => fetchData(2),
 *   );
 */
export function withConcurrency<T>(
  limit: number,
  ...tasks: Array<() => Promise<T>>
): Promise<Array<{ ok: true; value: T } | { ok: false; error: unknown }>> {
  const results: Array<{ ok: true; value: T } | { ok: false; error: unknown }> =
    new Array(tasks.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, async () => {
    for (;;) {
      const i = cursor++;
      if (i >= tasks.length) return;
      try {
        const value = await tasks[i]();
        results[i] = { ok: true, value };
      } catch (error) {
        results[i] = { ok: false, error };
      }
    }
  });
  return Promise.all(workers).then(() => results);
}