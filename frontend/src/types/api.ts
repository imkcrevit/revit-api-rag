/* Shared API types */

export interface RevitHealthResponse {
  revit_connected: boolean
  latency_ms: number | null
  detail: string
  bridge_version: string
  protocol?: string
  endpoint?: string
  timestamp: string
}

export interface OrchestratorQuestion {
  slot: string
  text: string
  options: string[]
  values?: string[]
  allow_custom?: boolean
}

export interface OrchestrateResponse {
  session_id: string
  status: string
  intent: Record<string, unknown>
  slots: Record<string, unknown>
  questions: OrchestratorQuestion[]
  action_plan: Array<Record<string, unknown>>
  summary: string
}

export interface ClassifyIntentResponse {
  interaction_type: string
  queries: Array<{ command: string; params: Record<string, unknown>; label?: string }>
  need_level: boolean
  select_prompt?: string
  parsed_coords?: { x: number; y: number; z?: number }
  quantity?: number
}

export interface GenerateStreamDone {
  code: string
  rag_context: Record<string, unknown>
  safe: boolean
  warnings: string[]
}

export interface ToolInfo {
  name: string
  display_name: string
  description: string
  parameters: ToolParam[]
  tags: string[]
  execution_count: number
}

export interface ToolParam {
  name: string
  description?: string
  type?: string
  default?: unknown
  choices_from?: string
}

export interface ToolChoiceItem {
  label: string
  value: string
}

export interface MatchToolResponse {
  matched: boolean
  name?: string
  display_name?: string
  description?: string
  parameters?: ToolParam[]
  has_params?: boolean
  has_dynamic_params?: boolean
  execution_count?: number
}

export interface ApiSearchResult {
  name: string
  full_id: string
  summary: string
  syntax: string
  parameters: string
  remark: string
  distance: number
}

export interface SdkSearchResult {
  project: string
  content: string
  mentioned_apis: string | string[]
  distance: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}
