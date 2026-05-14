/* MCP Bridge API calls — /api/v1/bridge/* */

import { apiGet, apiPost } from './client'
import type {
  RevitHealthResponse,
  ClassifyIntentResponse,
  OrchestrateResponse,
  MatchToolResponse,
  ToolInfo,
  ToolChoiceItem,
  GenerateStreamDone,
} from '../types/api'

const B = '/api/v1/bridge'

export const bridgeApi = {
  revitHealth: () => apiGet<RevitHealthResponse>(`${B}/revit-health`),

  classifyIntent: (query: string) =>
    apiPost<ClassifyIntentResponse>(`${B}/classify-intent`, { query }),

  orchestrate: (query: string, session_id?: string) =>
    apiPost<OrchestrateResponse>(`${B}/orchestrate`, { query, session_id }),

  queryRevit: (command: string, params: Record<string, unknown> = {}) =>
    apiPost<{ result: unknown[] }>(`${B}/query-revit`, { command, params }),

  matchTool: (query: string) =>
    apiPost<MatchToolResponse>(`${B}/match-tool`, { query }),

  execute: (code: string) =>
    apiPost<{ success: boolean; result: unknown; error: string }>(
      `${B}/execute`, { code }),

  solidify: (name: string, code: string, description: string,
    source_query: string, thinking: string, selections: Record<string, unknown>) =>
    apiPost(`${B}/parameterize`, { code, source_query, thinking, selections })
      .then((paramData: any) =>
        apiPost(`${B}/solidify`, {
          name, code: paramData.code || code, description,
          parameters: paramData.parameters || [],
          source_query,
        })),

  listTools: () => apiGet<ToolInfo[]>(`${B}/tools`),

  deleteTool: (name: string) =>
    apiFetch(`${B}/tools/${name}`, { method: 'DELETE' }),

  getToolDetail: (name: string) => apiGet<ToolInfo & { code_template: string }>(`${B}/tools/${name}`),

  getToolChoices: (name: string) =>
    apiGet<Record<string, ToolChoiceItem[]>>(`${B}/tools/${name}/choices`),

  runTool: (name: string, params: Record<string, string>) =>
    apiPost<{ success: boolean; result: unknown; error: string }>(
      `${B}/tools/${name}/run`, { name, params }),

  triggerSelection: () =>
    apiPost<{ elements: Array<Record<string, unknown>> }>(`${B}/trigger-selection`, {}),

  getUnit: () => apiGet<{ unit: string }>(`${B}/unit`),
  setUnit: (unit: string) => apiPost(`${B}/unit`, { unit }),
  getProjectUnits: () => apiGet<{ detected?: string; error?: string; current_setting: string }>(`${B}/project-units`),

  apiSearch: (query: string, top_k: number, fast: boolean) =>
    apiPost<{ rewritten_query: string; api_items: unknown[]; sdk_items: unknown[] }>(
      `${B}/api-search`, { query, top_k, fast }),

  apiCodegen: (api_name: string, api_context: string, user_hint: string) =>
    apiPost<{ code: string }>(`${B}/api-codegen`, { api_name, api_context, user_hint }),

  // Non-streaming generation (fallback)
  generateWithSelections: (query: string, selections: Record<string, unknown>) =>
    apiPost<GenerateStreamDone>(`${B}/generate-with-selections`, {
      query, selections, api_top_k: 15, code_top_k: 5 }),
}
