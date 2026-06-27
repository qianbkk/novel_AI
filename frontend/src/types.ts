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
  api_key: string | null;
  default_model: string | null;
  extra_json?: Record<string, unknown> | null;
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
  status: "pending" | "running" | "done" | "failed";
  exit_code: number | null;
}

export interface BridgeLogLine {
  event: "log" | "done" | "error"
       | "auto_pull_setting_start" | "auto_pull_setting_done"
       | "auto_import_chapters_start" | "auto_import_chapters_done"
       | "auto_chain_error";
  line?: string;
  message?: string;
  exit_code?: number;
  data?: unknown;
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
