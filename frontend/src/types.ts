export interface Project {
  id: string;
  title: string | null;
  genre: string;
  audience: string | null;
  status: "draft" | "worldbuilding" | "ready";
  budget_limit_usd?: number | null;
  novel_ai_status?: string;
  // 2026-07-23 修复（问题 #7）：后端 ProjectOut schema 现在返 created_at/updated_at。
  // 旧数据可能为 null（force_reimport 覆盖时未写时间），前端用 string | null 容错。
  created_at?: string | null;
  updated_at?: string | null;
  // 2026-07-24 修复（运行态可见性）：Dashboard 显示"运行中" badge 用。
  // 后端 /projects 现在附带当前 active BridgeRun 摘要；为 null 表示没在跑。
  active_run_command?: string | null;
  active_run_status?: "pending" | "running" | null;
  active_run_id?: string | null;
  active_run_started_at?: string | null;
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

/** 后端 GET /worldbuild/stages 返回的阶段清单（之前 WorldBuild.tsx 硬编码） */
export interface StageInfo {
  key: string;
  label: string;
}

export interface StageListOut {
  stages: StageInfo[];
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

// ─── 弧级大纲（ChapterTask = 每章任务单） ───
export interface ChapterTask {
  chapter_number: number;
  chapter_role: string;
  chapter_goal: string;
  main_characters: string[];
  shuang_type: string | null;
  shuang_description: string;
  ending_hook_type: string;
  ending_hook_description: string;
  setting_constraints: string[];
  forbidden_actions: string[];
  target_length: string;
  audit_mode: string;
  is_arc_climax: boolean;
}

export interface OutlineOut {
  id: string;
  project_id: string;
  arc_id: number;
  arc_name: string;
  arc_goal: string | null;
  arc_estimated_chapters: number;
  arc_climax_description: string | null;
  arc_climax_chapter_offset: number;
  arc_ending_state: string | null;
  emotion_curve: string;
  status: string;
  outline_json: ChapterTask[] | null;
  created_at: string;
  updated_at: string;
}

export interface OutlineCreatePayload {
  arc_id: number;
  arc_name: string;
  arc_goal?: string | null;
  arc_estimated_chapters?: number;
  arc_climax_description?: string | null;
  arc_climax_chapter_offset?: number;
  arc_ending_state?: string | null;
  emotion_curve?: string;
  outline_json?: ChapterTask[] | null;
}

export interface ArcGeneratePayload {
  arc_id: number;
  arc_name: string;
  arc_goal: string;
  arc_estimated_chapters?: number;
  arc_climax_description?: string | null;
  arc_climax_chapter_offset?: number;
  arc_ending_state?: string | null;
  emotion_curve?: string;
  genre?: string | null;
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

// ─── 分层记忆快照（GET /bridge/memory）───
// 之前前端"记忆层"是拿章节数/字数硬算的伪指标（还标了本项目根本不存在的
// L1/L3 层）。这套类型对应真实落盘的 L2 热/冷/约束 + L5 弧归档。
export interface MemoryForeshadowing {
  desc?: string;
  planted_at_chapter?: number;
  target_chapter?: number;
  target_arc?: number;
  due_chapter: number;
  overdue: boolean;
  [key: string]: unknown;
}

export interface MemoryStats {
  last_updated_chapter: number;
  total_chapters_tracked: number;
  chapters_per_arc: number | null;
  unverified_chapter_count: number;
  unverified_chapters: number[];
  foreshadowing_planted_count: number;
  foreshadowing_resolved_count: number;
  foreshadowing_overdue_count: number;
  active_thread_count: number;
  character_state_count: number;
  constraint_count: number;
  established_fact_count: number;
  recent_summaries_total: number;
  world_events_total: number;
  tracker_parse_failure_count: number;
  arc_count: number;
}

export interface BridgeMemory {
  available: boolean;
  l2_available: boolean;
  l5_available: boolean;
  novel_id?: string;
  message?: string;
  stats: MemoryStats;
  l2: {
    hot?: {
      protagonist_level?: string | null;
      protagonist_level_num?: number | null;
      protagonist_points?: number | null;
      inventory?: string[];
      active_threads?: string[];
      character_states?: Record<string, string>;
      recent_summaries?: Array<{ chapter?: number; summary?: string; unverified?: boolean }>;
      recent_events?: string;
      scene_location?: string | null;
      time_context?: string | null;
      last_chapter_ending?: string;
    };
    cold?: {
      world_events?: string[];
      closed_threads?: string[];
      resolved_foreshadowing?: string[];
    };
    constraints?: {
      forbidden_constraints?: Array<Record<string, unknown>>;
      established_facts?: Array<Record<string, unknown>>;
      foreshadowing_planted?: MemoryForeshadowing[];
    };
    meta?: {
      last_updated_chapter?: number | null;
      total_chapters_tracked?: number | null;
      chapters_per_arc?: number | null;
      tracker_parse_failure_count?: number;
      last_tracker_parse_failure_chapter?: number | null;
    };
  };
  l5: {
    arc_summaries?: Array<Record<string, unknown>>;
    character_arcs?: Record<string, string>;
    major_revelations?: string[];
    compressed_history?: string;
  };
}

// ════════════════════════════════════════════════════════════════════════════
// 世界构建板块结构化类型
// ════════════════════════════════════════════════════════════════════════════

// 角色卡 8 段
export interface CharacterCard {
  basic?: {
    gender?: string;
    age?: string | number;
    identity?: string;
    faction_id?: string | null;
  };
  appearance?: {
    height?: string;
    hair?: string;
    outfit?: string;
    distinguishing_feature?: string;
  };
  personality?: {
    tags: string[];
    summary: string;
  };
  background?: {
    origin?: string;
    motivation?: string;
    secret?: string;
  };
  abilities?: {
    power_name?: string;
    current_tier?: string;
    growth_potential?: string;
  } | null;
  catchphrase?: {
    lines: string[];
  };
  props?: {
    signature_item?: string;
    companion?: string;
  } | null;
  arc: {
    start_state: string;
    catalyst: string;
    end_state: string;
  };
}

// GET /projects/{pid}/characters/{cid} 详情
export interface CharacterCardOut {
  id: string;
  name: string;
  role: string | null;
  card: CharacterCard | null;
  faction?: { id: string; name: string } | null;
}

// GET /projects/{pid}/characters 列表摘要
export interface CharacterSummary {
  id: string;
  name: string;
  role: string | null;
  identity?: string | null;
  age?: string | number | null;
  gender?: string | null;
  // 2026-07-23 修复（前端一致性）：personality.summary 字段
  // 之前 schema 缺，前端 Characters 列表显示"无内容"
  personality_summary?: string | null;
}

// 关系图谱节点 / 边 / 全图
export interface GraphNode {
  id: string;
  name: string;
  role: string | null;
  role_kind: string;
}

export interface GraphEdge {
  from_id: string;
  /** 2026-07-27：后端补上 from_type / to_type，FactionGraph 需要它们区分
   *  角色→势力 边。之前缺这两字段时，FactionGraph 用 i%5 合成边，
   *  用户截图会出现"图上敌对但世界构建文本里没写"的虚假关系。 */
  from_type?: string;
  to_id: string;
  to_type?: string;
  relation: string;
  mutual: boolean;
  intensity: number | null;
  tags: string[] | null;
}

export interface RelationGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// 角色关系边详情
export interface CharacterRelation {
  id: string;
  relation: string;
  description: string | null;
  target: { id: string; name: string; role: string | null };
  mutual: boolean;
  intensity: number | null;
  tags: string[] | null;
  evolution: { phase: string; state: string }[] | null;
  key_events: { chapter_hint: string; event: string }[] | null;
}

// 世界观结构化 7 段
export interface WorldviewRich {
  cosmos: string;
  geography: string;
  history: string;
  society: string;
  technology: string;
  races: string;
  customs: string;
}

export interface StoryCoreStruct {
  goal?: string;
  conflict?: string;
  theme?: string;
  hook?: string;
}

export interface HistoryTimelineNode {
  era: string;
  event: string;
  impact: string;
}

// GET /worldview/rich 响应
export interface WorldviewRichOut {
  rich: WorldviewRich | null;
  story_core: StoryCoreStruct | null;
  history_timeline: HistoryTimelineNode[] | null;
  fallback_text: string | null;
  fallback_story_core: string | null;
}

// ─── 多用户认证类型 ───
export interface User {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
}

export interface AuthErrorEvent {
  /** CustomEvent detail for "auth required" signals from api client. */
  reason: "missing" | "expired";
}
