/* Tab: PromptBridge — 设计师提示词优化助手 */

import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../../types/api'
import { useSettingsStore } from '../../store'

const QUICK_PROMPTS = [
  { label: '创建房间 / Create Room', text: '创建一个标准间' },
  { label: 'Place Column', text: 'Place a structural column at the center' },
  { label: '画墙 / Draw Wall', text: '画一面墙' },
  { label: 'Query Elements', text: 'How many walls are in the current view?' },
  { label: '布局设计 / Layout', text: '帮我布置一个办公区' },
  { label: 'Modify Element', text: 'Make this wall 500mm taller' },
]

export default function PromptBridgeTab() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const { sessionId } = useSettingsStore()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = useCallback(async (text?: string) => {
    const msg = (text ?? input).trim()
    if (!msg || streaming) return
    if (!text) setInput('')
    setMessages(prev => [...prev, { role: 'user', content: msg }])
    setStreaming(true)

    const abort = new AbortController()
    abortRef.current = abort

    try {
      const resp = await fetch('/api/prompt-bridge/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, session_id: sessionId }),
        signal: abort.signal,
      })
      if (!resp.ok) throw new Error(`Error ${resp.status}`)
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
          if (dataStr.trim() === '[DONE]') break
          try {
            const token = JSON.parse(dataStr)
            content += token
            setMessages(prev => {
              const copy = [...prev]
              if (copy.length && copy[copy.length - 1].role === 'assistant') {
                copy[copy.length - 1] = { role: 'assistant', content }
              } else {
                copy.push({ role: 'assistant', content })
              }
              return copy
            })
          } catch { /* skip */ }
        }
      }

      if (!content) {
        setMessages(prev => [...prev, { role: 'assistant', content: '(无响应)' }])
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setMessages(prev => [...prev, { role: 'assistant', content: `错误：${e.message}` }])
      }
    } finally {
      setStreaming(false)
    }
  }, [input, streaming, sessionId])

  const handleClear = () => {
    setMessages([])
    fetch('/api/prompt-bridge/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    }).catch(() => {})
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).catch(() => {})
  }

  const isEmpty = messages.length === 0

  return (
    <div className="flex flex-col h-full">
      {/* Messages / Welcome */}
      <div className="flex-1 overflow-y-auto p-4 min-h-[420px]">
        {isEmpty ? (
          <div style={{ maxWidth: 600, margin: '60px auto', textAlign: 'center' }}>
            <h2 style={{
              fontFamily: 'var(--display)',
              fontSize: 20,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              color: 'var(--dark)',
              marginBottom: 8,
            }}>
              PromptBridge
            </h2>
            <p style={{
              fontFamily: 'var(--serif)',
              fontSize: 15,
              color: 'var(--mid)',
              marginBottom: 32,
              lineHeight: 1.6,
            }}>
              Describe what you need in your own words — I'll refine it into a precise AI prompt. 用你习惯的方式描述需求，我来帮你优化。
            </p>

            {/* Quick prompts */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 10,
              marginBottom: 24,
            }}>
              {QUICK_PROMPTS.map(q => (
                <button
                  key={q.label}
                  onClick={() => send(q.text)}
                  disabled={streaming}
                  style={{
                    fontFamily: 'var(--mono)',
                    fontSize: 12,
                    padding: '14px 12px',
                    background: 'var(--bg)',
                    border: '1px solid var(--subtle)',
                    borderRadius: 2,
                    cursor: 'pointer',
                    textAlign: 'left',
                    transition: 'all 0.2s',
                    color: 'var(--dark)',
                  }}
                  onMouseEnter={e => {
                    e.currentTarget.style.borderColor = 'var(--accent)'
                    e.currentTarget.style.color = 'var(--accent)'
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.borderColor = 'var(--subtle)'
                    e.currentTarget.style.color = 'var(--dark)'
                  }}
                >
                  <div style={{ fontWeight: 500, marginBottom: 4 }}>{q.label}</div>
                  <div style={{ fontSize: 11, color: 'var(--faint)' }}>{q.text}</div>
                </button>
              ))}
            </div>

            <p style={{
              fontFamily: 'var(--mono)',
              fontSize: 11,
              color: 'var(--faint)',
              letterSpacing: '0.03em',
            }}>
              Click an example to get started, or type your own request / 点击示例快速开始，或直接输入
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className="max-w-[85%] px-4 py-2 group relative"
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
                    <div className="prompt-bridge-response">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}
                        components={{
                          // 给 code block 加一个复制按钮
                          code({ children, className }) {
                            const isBlock = className?.includes('language-')
                            if (!isBlock) {
                              return <code style={{
                                fontFamily: 'var(--mono)',
                                fontSize: 12,
                                background: 'var(--bg3)',
                                padding: '1px 4px',
                                borderRadius: 2,
                              }}>{children}</code>
                            }
                            const text = String(children).replace(/\n$/, '')
                            return (
                              <div style={{ position: 'relative', margin: '8px 0' }}>
                                <pre style={{
                                  fontFamily: 'var(--mono)',
                                  fontSize: 12,
                                  background: 'var(--bg3)',
                                  border: '1px solid var(--line)',
                                  borderRadius: 2,
                                  padding: '12px 14px',
                                  overflowX: 'auto',
                                  whiteSpace: 'pre-wrap',
                                  lineHeight: 1.6,
                                }}>
                                  <code>{text}</code>
                                </pre>
                                <button
                                  onClick={() => copyToClipboard(text)}
                                  style={{
                                    position: 'absolute',
                                    top: 6,
                                    right: 6,
                                    fontFamily: 'var(--mono)',
                                    fontSize: 10,
                                    padding: '3px 8px',
                                    background: 'var(--bg)',
                                    border: '1px solid var(--subtle)',
                                    borderRadius: 2,
                                    cursor: 'pointer',
                                    color: 'var(--mid)',
                                    letterSpacing: '0.05em',
                                    textTransform: 'uppercase',
                                  }}
                                >
                                  Copy
                                </button>
                              </div>
                            )
                          },
                          table({ children }) {
                            return (
                              <div style={{ overflowX: 'auto', margin: '8px 0' }}>
                                <table style={{
                                  fontFamily: 'var(--mono)',
                                  fontSize: 12,
                                  borderCollapse: 'collapse',
                                  width: '100%',
                                }}>
                                  {children}
                                </table>
                              </div>
                            )
                          },
                          th({ children }) {
                            return <th style={{
                              padding: '6px 10px',
                              borderBottom: '2px solid var(--line)',
                              textAlign: 'left',
                              fontWeight: 600,
                              color: 'var(--dark)',
                            }}>{children}</th>
                          },
                          td({ children }) {
                            return <td style={{
                              padding: '6px 10px',
                              borderBottom: '1px solid var(--line)',
                              color: 'var(--mid)',
                            }}>{children}</td>
                          },
                        }}
                      >{m.content}</ReactMarkdown>
                    </div>
                  ) : m.content}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="flex items-center gap-2 p-3" style={{ borderTop: '1px solid var(--line)' }}>
        <input
          type="text"
          className="input-field flex-1"
          placeholder="Describe what you want to do in Revit... / 描述你想在 Revit 中做什么..."
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
          disabled={streaming}
        />
        <button onClick={() => send()} disabled={streaming} className="btn-primary">
          Send
        </button>
        <button onClick={handleClear} className="btn-secondary">
          Clear
        </button>
      </div>
    </div>
  )
}
