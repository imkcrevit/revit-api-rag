/* Base API client — same-origin by default, configurable for split deployment. */

const RAW_BASE = import.meta.env.VITE_API_BASE_URL || ''
const BASE = RAW_BASE.replace(/\/+$/, '')

function _slotHeader(): Record<string, string> {
  const slot = sessionStorage.getItem('mcp_slot')
  const token = sessionStorage.getItem('mcp_slot_token')
  if (!slot) return {}
  return {
    'X-Slot-Id': slot,
    ...(token ? { 'X-Slot-Token': token } : {}),
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ..._slotHeader(), ...init?.headers },
  })
  if (!resp.ok) {
    const raw = await resp.text().catch(() => '')
    const isHtml = raw.trimStart().startsWith('<') || resp.headers.get('content-type')?.includes('text/html')
    const detail = isHtml
      ? 'Backend unreachable (received HTML error page instead of JSON). Is the server running?'
      : raw.slice(0, 300)
    throw new Error(`${resp.status}: ${detail}`)
  }
  return resp.json()
}

export function apiPost<T = unknown>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: 'POST', body: JSON.stringify(body) })
}

export function apiGet<T = unknown>(path: string): Promise<T> {
  return apiFetch<T>(path)
}

/* SSE stream helper — yields parsed events */
export interface SSEEvent {
  event: string
  data: string
}

export async function* sseStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._slotHeader() },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok) {
    const raw = await resp.text().catch(() => '')
    const isHtml = raw.trimStart().startsWith('<')
    throw new Error(isHtml
      ? `SSE ${resp.status}: Backend unreachable`
      : `SSE ${resp.status}: ${raw.slice(0, 200)}`)
  }
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop()!

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7)
      } else if (line.startsWith('data: ')) {
        yield { event: currentEvent, data: line.slice(6) }
        currentEvent = ''
      }
    }
  }
}

/* Token stream helper — POSTs to an SSE endpoint and yields individual decoded
   tokens. Terminates cleanly on the `[DONE]` sentinel (fixes inner-loop break
   bugs) and shares one parser across chat-style callers. */
export async function* tokenStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<string> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._slotHeader() },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok) {
    const raw = await resp.text().catch(() => '')
    const isHtml = raw.trimStart().startsWith('<')
    throw new Error(isHtml
      ? `${resp.status}: Backend unreachable`
      : `${resp.status}: ${raw.slice(0, 200)}`)
  }
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()!

    for (const line of lines) {
      if (line.startsWith('event: ')) continue
      if (!line.startsWith('data: ')) continue
      const dataStr = line.slice(6)
      if (dataStr.trim() === '[DONE]') return
      try {
        yield JSON.parse(dataStr)
      } catch { /* skip malformed */ }
    }
  }
}
