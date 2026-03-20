/* Reusable chat panel — graptolite.ai style */

import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../../types/api'
import { chatStream } from '../../api/settings'
import { useSettingsStore } from '../../store'

interface Props {
  endpoint: string
  placeholder: string
  showFullOption?: boolean
}

export default function ChatPanel({ endpoint, placeholder, showFullOption = false }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const { sessionId, showFull } = useSettingsStore()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = useCallback(async () => {
    const msg = input.trim()
    if (!msg || streaming) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: msg }])
    setStreaming(true)

    const abort = new AbortController()
    abortRef.current = abort

    try {
      let lastContent = ''
      for await (const content of chatStream(
        endpoint, msg, sessionId,
        showFullOption ? showFull : undefined,
        abort.signal
      )) {
        lastContent = content
        setMessages(prev => {
          const copy = [...prev]
          if (copy.length && copy[copy.length - 1].role === 'assistant') {
            copy[copy.length - 1] = { role: 'assistant', content }
          } else {
            copy.push({ role: 'assistant', content })
          }
          return copy
        })
      }
      if (!lastContent) {
        setMessages(prev => [...prev, { role: 'assistant', content: '(no response)' }])
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${e.message}` }])
      }
    } finally {
      setStreaming(false)
    }
  }, [input, streaming, endpoint, sessionId, showFull, showFullOption])

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-[420px]">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className="max-w-[80%] px-4 py-2"
              style={{
                borderRadius: 2,
                fontFamily: m.role === 'user' ? 'var(--mono)' : 'var(--serif)',
                fontSize: m.role === 'user' ? 13 : 14,
                background: m.role === 'user' ? 'var(--accent)' : 'var(--bg2)',
                color: m.role === 'user' ? '#fff' : 'var(--dark)',
                border: m.role === 'user' ? 'none' : '1px solid var(--line)',
              }}
            >
              {m.role === 'assistant' ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              ) : m.content}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex items-center gap-2 p-3" style={{ borderTop: '1px solid var(--line)' }}>
        <input
          type="text"
          className="input-field flex-1"
          placeholder={placeholder}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
          disabled={streaming}
        />
        <button onClick={send} disabled={streaming} className="btn-primary">
          Send
        </button>
        <button onClick={() => setMessages([])} className="btn-secondary">
          Clear
        </button>
      </div>
    </div>
  )
}
