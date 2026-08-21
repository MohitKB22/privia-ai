/** Types mirroring `privia_shared`. Kept in one file so drift is obvious. */

export type RiskLevel = 'none' | 'low' | 'medium' | 'high' | 'critical';
export type RunStatus =
  'pending' | 'running' | 'awaiting_confirmation' | 'completed' | 'failed' | 'denied';
export type ProcessingLocation = 'local' | 'cloud' | 'none';
export type GrantState = 'granted' | 'denied' | 'not_requested' | 'expired';
export type IntegrationStatus =
  'ready' | 'not_configured' | 'unavailable' | 'auth_required' | 'error';

export interface ApiError {
  error: {
    code: string;
    message: string;
    request_id: string | null;
    details: Record<string, unknown>;
  };
}

export interface ToolCall {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  risk_level: RiskLevel;
  justification: string;
  requires_confirmation: boolean;
}

export interface ToolResult {
  call_id: string;
  tool_name: string;
  success: boolean;
  data: unknown;
  error: string | null;
  error_code: string | null;
  duration_ms: number;
  metadata: Record<string, unknown>;
  accessed_resources: string[];
  truncated: boolean;
}

export interface ConfirmationRequest {
  id: string;
  run_id: string;
  tool_name: string;
  title: string;
  summary: string;
  risk_level: RiskLevel;
  details: Record<string, string>;
  target: string | null;
  destructive: boolean;
}

export interface PermissionPrompt {
  tool_name: string;
  missing_scopes: string[];
  resources: string[];
  out_of_scope_resources: string[];
  rationale: string;
}

export interface ChatResponse {
  run_id: string;
  request_id: string;
  session_id: string;
  response: string;
  status: RunStatus;
  intent: string;
  processing_location: ProcessingLocation;
  model_used: string | null;
  tool_calls: ToolCall[];
  tool_results: ToolResult[];
  pending_confirmation: ConfirmationRequest | null;
  accessed_resources: string[];
  permission_prompt: PermissionPrompt | null;
  duration_ms: number;
  audio_base64: string | null;
}

export interface ModelInfo {
  provider: string;
  model: string;
  available: boolean;
  location: string;
  detail: string;
  latency_ms: number | null;
}

export interface IntegrationInfo {
  name: string;
  family: string;
  provider: string;
  status: IntegrationStatus;
  capabilities: string[];
  detail: string;
  authenticated: boolean;
}

export interface StatusResponse {
  version: string;
  app_env: string;
  uptime_seconds: number;
  python: string;
  platform: string;
  database: { path: string; schema_version: number; size_bytes: number };
  models: {
    local: ModelInfo;
    cloud: ModelInfo | null;
    embeddings: { model: string; dimensions: number; local: boolean };
  };
  speech: { stt: IntegrationInfo; tts: IntegrationInfo };
  integrations: IntegrationInfo[];
  privacy: {
    cloud_processing_enabled: boolean;
    memory_enabled: boolean;
    telemetry_enabled: boolean;
    data_leaving_device: boolean;
  };
  tools: number;
  warnings: string[];
}

export interface ScopeInfo {
  scope: string;
  family: string;
  description: string;
  state: GrantState;
  resources: string[];
  session_only: boolean;
  expires_at: string | null;
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  action: string;
  session_id: string | null;
  run_id: string | null;
  request_id: string | null;
  actor: string;
  tool_name: string | null;
  target: string | null;
  outcome: 'success' | 'failure' | 'denied' | 'pending';
  detail: Record<string, unknown>;
}

export interface Note {
  id: string;
  title: string;
  body: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  pinned: boolean;
}

export interface MemoryRecord {
  id: string;
  kind: string;
  content: string;
  tags: string[];
  provenance: string;
  created_at: string;
  updated_at: string;
  use_count: number;
  pinned: boolean;
  score: number | null;
}

export interface FileEntry {
  path: string;
  name: string;
  is_dir: boolean;
  size_bytes: number;
  modified_at: string | null;
  extension: string;
  mime_type: string | null;
}

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  timezone: string;
  all_day: boolean;
  location: string | null;
  description: string | null;
  participants: string[];
  calendar: string;
  cancelled: boolean;
}

export interface EmailDraft {
  id: string;
  to: { address: string; name: string | null }[];
  cc: { address: string; name: string | null }[];
  subject: string;
  body: string;
  status: 'draft' | 'sent' | 'failed';
  created_at: string;
  updated_at: string;
  sent_at: string | null;
}

export interface ToolSpec {
  name: string;
  family: string;
  description: string;
  scopes: string[];
  risk_level: RiskLevel;
  requires_confirmation: boolean;
  timeout_seconds: number;
  returns_untrusted_content: boolean;
  input_schema: Record<string, unknown>;
}

export interface PrivacyState {
  local_processing: boolean;
  cloud_processing: boolean;
  cloud_provider: string | null;
  current_llm: ModelInfo | null;
  current_embedding_model: string;
  stt_available: boolean;
  tts_available: boolean;
  telemetry_enabled: boolean;
  memory_enabled: boolean;
  data_leaving_device: boolean;
  allowed_directories: string[];
  terminal_roots: string[];
  integrations: IntegrationInfo[];
  grants: { scope: string; state: string; resources: string[]; granted: boolean }[];
  recent_activity: AuditEvent[];
  database_path: string;
}

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
  runId?: string;
  toolCalls?: ToolCall[];
  toolResults?: ToolResult[];
  accessed?: string[];
  location?: ProcessingLocation;
  model?: string | null;
  durationMs?: number;
  pending?: boolean;
  failed?: boolean;
};
