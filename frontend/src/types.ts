export interface Project {
  id: string;
  title: string | null;
  genre: string;
  audience: string | null;
  status: "draft" | "worldbuilding" | "ready";
  budget_limit_usd?: number | null;
  novel_ai_status?: string;
}

export interface JobStatus {
  id: string;
  project_id: string;
  status: "pending" | "running" | "done" | "failed";
  current_stage: string | null;
  progress_percent: number;
}

export interface ConsistencyWarning {
  type: string;
  detail: string[];
}

export interface Character {
  id: string;
  name: string;
  role: string | null;
  detail_json: Record<string, unknown> | null;
}

export interface EntityRelation {
  id: string;
  from_type: string;
  from_id: string;
  to_type: string;
  to_id: string;
  relation: string;
  description: string | null;
}

export interface Faction {
  id: string;
  name: string;
  detail_json: Record<string, unknown> | null;
}

export interface PowerSystem {
  id: string;
  name: string;
  description: string | null;
  tiers_json: { level: number; name: string }[] | null;
}

export interface MapNode {
  id: string;
  parent_id: string | null;
  name: string;
  level: string;
  description: string | null;
}

export interface Foreshadowing {
  id: string;
  content: string;
  linked_character_id: string | null;
  importance: string;
  status: string;
  planted_chapter_hint?: string | null;
  payoff_chapter_hint?: string | null;
}

export interface Currency {
  id: string;
  name: string;
  detail_json: Record<string, unknown> | null;
}

export interface WorldSetting {
  world_view: string | null;
  story_core: string | null;
  plot_skeleton_json: { title: string; summary: string }[] | null;
  special_settings_json: Record<string, unknown> | null;
}

export interface WorldBuildResult {
  world_setting: WorldSetting | null;
  characters: Character[];
  relations: EntityRelation[];
  factions: Faction[];
  power_systems: PowerSystem[];
  map_nodes: MapNode[];
  foreshadowings: Foreshadowing[];
  currencies: Currency[];
  consistency_warnings: ConsistencyWarning[];
}

export interface ChapterListItem {
  id: string;
  chapter_no: number;
  title: string | null;
  content_preview: string;
  word_count: number;
  created_at: string;
}

export interface RepetitionWarning {
  compared_chapter_id: string;
  similarity: number;
}

export interface ChapterCreateResult {
  chapter_id: string;
  repetition_warnings: RepetitionWarning[];
}

export interface ChapterSearchResult {
  chapter_id: string;
  similarity: number;
  snippet: string;
}

export interface Provider {
  id: string;
  name: string;
  provider_type: "anthropic" | "deepseek" | "gemini" | "kimi" | "minimax" | "custom";
  api_base: string | null;
  // 重要：API 返回**绝不**包含明文 api_key（commit 加密后）。
  // 后端只返回 api_key_set（布尔）+ api_key_suffix（明文后 4 位，仅供 UI 显示）。
  // 因此 Provider 类型里**没有** api_key 字段——避免 TypeScript 误以为可以
  // 直接读它、或意外 spread 到 PUT 请求体里把已有 key 清空。
  // 编辑表单需要单独定义 ProviderForm 类型（见 Providers.tsx）。
  api_key_set?: boolean;
  api_key_suffix?: string | null;
  default_model: string | null;
  extra_json?: Record<string, unknown> | null;
  needs_proxy: boolean;
}

/** Provider 的编辑表单类型（包含明文 api_key 字段）。
 *  与 Provider 区分开：Provider 来自 API（无 api_key），ProviderForm 是用户
 *  在表单里输入的（必含 api_key，方便校验「编辑时是否填了 key」）。
 */
export interface ProviderForm {
  name: string;
  provider_type: Provider["provider_type"];
  api_base: string;
  api_key: string;
  default_model: string;
  extra_json: Record<string, unknown> | null;
  needs_proxy: boolean;
}

/** Provider 创建/更新请求体（对应后端 ProviderCreate schema）。
 *  与 ProviderForm 的区别：默认值规范化（null vs 空字符串）。
 */
export interface ProviderCreate {
  name: string;
  provider_type: Provider["provider_type"];
  api_base: string | null;
  api_key: string;
  default_model: string | null;
  extra_json: Record<string, unknown> | null;
  needs_proxy: boolean;
}

export interface RoleAssignment {
  role_key: string;
  label: string;
  provider_id: string | null;
  provider_name: string | null;
  provider_type?: Provider["provider_type"] | null;
  model_override: string | null;
}

export interface BridgeRun {
  id: string;
  project_id: string;
  command: string;
  args_json?: Record<string, unknown> | unknown[] | null;
  status: "pending" | "running" | "done" | "failed";
  exit_code: number | null;
  // SSE 断了也能查的兜底字段（commit 62baf44 subprocess 后由主进程定期 flush 到 DB）
  stdout_text?: string | null;
  // ISO datetime 字符串
  started_at: string;
  finished_at: string | null;
  outline_mode?: string;  // batch | card | talk
}

export interface BridgeLogLine {
  event: "log" | "done" | "error"
       | "start" | "complete"
       | "auto_pull_setting_start" | "auto_pull_setting_done"
       | "auto_import_chapters_start" | "auto_import_chapters_done"
       | "auto_chain_error"
       | "node_start" | "node_end";
  line?: string;
  message?: string;
  exit_code?: number;
  data?: unknown;
  node?: string;  // node_start / node_end 事件携带
  command?: string;       // start 事件携带
  outline_mode?: string;  // start 事件携带
  status?: string;        // complete 事件携带
  imported?: unknown;     // auto_import_chapters_done 事件携带
  traceback?: string;     // error 事件携带
}

export interface BridgeStatus {
  status?: string;
  current_stage?: string;
  current_chapter?: number;
  total_chapters?: number;
  human_pending?: BridgePendingItem[];
  [key: string]: unknown;
}

export interface BridgePendingItem {
  task_id: string;
  title?: string;
  type?: string;
  content?: string;
  chapter_no?: number;
  status?: string;
  [key: string]: unknown;
}

export interface NovelAIBinding {
  project_id: string;
  novel_ai_dir: string;
  novel_id: string;
}

/** SSE 推送过来的事件，对应后端 orchestrator.py 里 queue.put 的几种 payload */
export interface StageEvent {
  event: "stage_start" | "stage_done" | "job_done" | "job_failed";
  stage?: string;
  label?: string;
  progress_percent?: number;
  consistency_warnings?: ConsistencyWarning[];
  error?: string;
}

// ─── 规则中心（RuleCenter）───
export interface RuleConfig {
  project_id: string;
  style: "webnovel" | "literary" | "wuxia";
  taboos: string[];
  template: string;
  extra?: Record<string, unknown>;
  updated_at?: string | null;
}

export interface PostProcessResult {
  tool: "logic" | "venom" | "deai";
  chapter_no?: number | null;
  summary: string;
  findings: { line?: string; [k: string]: unknown }[];
  score?: number | null;
  cost_usd: number;
  generated_at: string;
}

// ─── 章节详情 + 出场人物 ───
export interface ChapterCharacter {
  id: string;
  character_id: string;
  character_name?: string | null;
  character_role?: string | null;
}

export interface ChapterFull {
  id: string;
  chapter_no: number;
  title: string | null;
  content: string;
  // 后端 schema 允许 None（历史数据兼容：raw SQL / _force_reimport 覆盖写入的
  // 旧行 created_at 可能为 null），前端用 string | null 跟 schema 对齐。
  created_at: string | null;
  characters: ChapterCharacter[];
}

// ─── 伏笔状态 ───
export interface ForeshadowingRow {
  id: string;
  content: string;
  importance: string;
  status: "未铺垫" | "已铺垫" | "已回收";
  linked_character_id: string | null;
  planted_chapter_hint?: string | null;
  payoff_chapter_hint?: string | null;
}

// ─── AI 参与度 ───
export interface AiAssistLevel {
  project_id: string;
  ai_assist_level: "ai_assisted" | "human_primary" | "unset";
}

// ─── 预算（修正：后端返回单个对象，不是数组）───
export interface BridgeBudget {
  available: boolean;
  budget_limit_usd?: number | null;
  total_cost_usd: number;
  record_count: number;
  records: Array<{
    ts: string;
    chapter: number;
    arc: number;
    agent: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }>;
  [key: string]: unknown;
}
