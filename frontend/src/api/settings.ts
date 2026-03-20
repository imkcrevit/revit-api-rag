/* Settings & Chat API calls */

import { apiPost } from './client'

export const settingsApi = {
  update: (api_key: string, model: string, _session_id: string) =>
    apiPost('/api/settings', { api_key, model: model || null }),
}

/* Chat SSE stream for Tab A / Tab B */
export async function* chatStream(
  endpoint: string,
  message: string,
  session_id: string,
  show_full?: boolean,
  signal?: AbortSignal,
): AsyncGenerator<string> {
  const body: Record<string, unknown> = { message, session_id }
  if (show_full !== undefined) body.show_full = show_full

  const resp = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok) throw new Error(`Chat ${resp.status}`)
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let content = ''

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
        const token = JSON.parse(dataStr)
        content += token
        yield content
      } catch { /* skip */ }
    }
  }
}
