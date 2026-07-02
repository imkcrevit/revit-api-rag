/* Tab: PromptBridge — designer prompt refinement with inline corrections + option cards */

import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../../types/api'
import { tokenStream } from '../../api/client'
import { useSettingsStore } from '../../store'
import { getErrorMessage, isAbortError } from '../../utils/errors'

/* ── Quick prompts shown on welcome screen ── */
const QUICK_PROMPTS = [
  { label: '创建房间 / Create Room', text: '创建一个标准间' },
  { label: 'Place Column', text: 'Place a structural column at the center' },
  { label: '画墙 / Draw Wall', text: '画一面墙' },
  { label: 'Query Elements', text: 'How many walls are in the current view?' },
  { label: '布局设计 / Layout', text: '帮我布置一个办公区' },
  { label: 'Modify / 修改', text: 'Make this wall 500mm taller' },
]

/* ── Parse [OPTION: title] and [CHOICE: title] blocks from LLM output ── */
interface ParsedBlock { title: string; content: string }

function parseBlocks(text: string, tag: 'OPTION' | 'CHOICE'): {
  before: string; blocks: ParsedBlock[]; after: string
} | null {
  const regex = new RegExp(
    `\\[${tag}:\\s*(.+?)\\]\\s*\\n([\\s\\S]*?)(?=\\n\\[${tag}:|\\n\\[(?:OPTION|CHOICE):|$)`,
    'g',
  )
  const matches = [...text.matchAll(regex)]
  if (matches.length === 0) return null

  const firstIdx = matches[0].index!
  const before = text.slice(0, firstIdx).trim()
  const lastMatch = matches[matches.length - 1]
  const lastEnd = lastMatch.index! + lastMatch[0].length
  const after = text.slice(lastEnd).trim()

  return {
    before,
    blocks: matches.map(m => ({ title: m[1].trim(), content: m[2].trim() })),
    after,
  }
}

/* ── Card styles ── */
const cardBase: React.CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 12,
  padding: '14px 14px 10px',
  background: 'var(--bg)',
  border: '1px solid var(--subtle)',
  borderRadius: 'var(--radius-sm)',
  textAlign: 'left',
  transition: 'all 0.2s',
  color: 'var(--dark)',
  position: 'relative',
  cursor: 'default',
}

const copyBtnStyle: React.CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 10,
  padding: '3px 10px',
  background: 'var(--accent)',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  cursor: 'pointer',
  color: '#fff',
  letterSpacing: '0.05em',
  textTransform: 'uppercase',
}

/* ── Main component ── */
export default function PromptBridgeTab() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const { sessionId } = useSettingsStore()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Abort in-flight stream on unmount
  useEffect(() => () => { abortRef.current?.abort() }, [])

  const send = useCallback(async (text?: string) => {
    const msg = (text ?? input).trim()
    if (!msg || streaming) return
    if (!text) setInput('')
    setMessages(prev => [...prev, { role: 'user', content: msg }])
    setStreaming(true)

    const abort = new AbortController()
    abortRef.current = abort

    try {
      let content = ''
      for await (const token of tokenStream(
        '/api/prompt-bridge/chat',
        { message: msg, session_id: sessionId },
        abort.signal,
      )) {
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
      }

      if (!content) {
        setMessages(prev => [...prev, { role: 'assistant', content: '(No response / 无响应)' }])
      }
    } catch (e: unknown) {
      if (!isAbortError(e)) {
        setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${getErrorMessage(e)}` }])
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

  const copyToClipboard = (text: string, idx?: number) => {
    navigator.clipboard.writeText(text).then(() => {
      if (idx !== undefined) {
        setCopiedIdx(idx)
        setTimeout(() => setCopiedIdx(null), 1500)
      }
    }).catch(() => {})
  }

  /* ── Render a markdown section (corrections text etc.) ── */
  const renderMarkdown = (text: string) => (
    <ReactMarkdown remarkPlugins={[remarkGfm]}
      components={{
        code({ children, className }) {
          const isBlock = className?.includes('language-')
          if (!isBlock) {
            return <code style={{
              fontFamily: 'var(--mono)', fontSize: 12,
              background: 'var(--bg3)', padding: '1px 4px', borderRadius: 'var(--radius-sm)',
            }}>{children}</code>
          }
          const text = String(children).replace(/\n$/, '')
          return (
            <div style={{ position: 'relative', margin: '8px 0' }}>
              <pre style={{
                fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--bg3)',
                border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
                padding: '12px 14px', overflowX: 'auto', whiteSpace: 'pre-wrap', lineHeight: 1.6,
              }}><code>{text}</code></pre>
              <button onClick={() => copyToClipboard(text)}
                style={{ ...copyBtnStyle, position: 'absolute', top: 6, right: 6 }}>Copy</button>
            </div>
          )
        },
      }}
    >{text}</ReactMarkdown>
  )

  /* ── Render OPTION cards (copyable prompt cards) ── */
  const renderOptionCards = (blocks: ParsedBlock[]) => (
    <div style={{
      display: 'grid',
      gridTemplateColumns: blocks.length === 1 ? '1fr' : 'repeat(auto-fill, minmax(260px, 1fr))',
      gap: 10, margin: '12px 0',
    }}>
      {blocks.map((b, i) => (
        <div key={i} style={{
          ...cardBase,
          borderColor: copiedIdx === i ? 'var(--accent)' : 'var(--subtle)',
        }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)' }}
          onMouseLeave={e => { if (copiedIdx !== i) e.currentTarget.style.borderColor = 'var(--subtle)' }}
        >
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8, color: 'var(--dark)' }}>
            {b.title}
          </div>
          <div style={{
            fontSize: 12, color: 'var(--mid)', lineHeight: 1.6,
            marginBottom: 10, whiteSpace: 'pre-wrap',
          }}>
            {b.content}
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button onClick={() => copyToClipboard(b.content, i)} style={copyBtnStyle}>
              {copiedIdx === i ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
      ))}
    </div>
  )

  /* ── Render CHOICE cards (clickable disambiguation cards) ── */
  const renderChoiceCards = (blocks: ParsedBlock[]) => (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
      gap: 10, margin: '12px 0',
    }}>
      {blocks.map((b, i) => (
        <button key={i} onClick={() => send(b.title)} disabled={streaming}
          style={{
            ...cardBase,
            cursor: 'pointer',
            display: 'block',
            width: '100%',
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
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{b.title}</div>
          <div style={{ fontSize: 11, color: 'var(--faint)', lineHeight: 1.5 }}>{b.content}</div>
        </button>
      ))}
    </div>
  )

  /* ── Render a full assistant message with parsed blocks ── */
  const renderAssistantMessage = (content: string) => {
    const options = parseBlocks(content, 'OPTION')
    const choices = parseBlocks(content, 'CHOICE')

    // Has OPTION blocks
    if (options) {
      return (
        <>
          {options.before && renderMarkdown(options.before)}
          {renderOptionCards(options.blocks)}
          {options.after && renderMarkdown(options.after)}
        </>
      )
    }

    // Has CHOICE blocks (disambiguation)
    if (choices) {
      return (
        <>
          {choices.before && renderMarkdown(choices.before)}
          {renderChoiceCards(choices.blocks)}
          {choices.after && renderMarkdown(choices.after)}
        </>
      )
    }

    // Fallback: plain markdown
    return renderMarkdown(content)
  }

  const isEmpty = messages.length === 0

  return (
    <div className="flex flex-col h-full">
      {/* Correction styling: ~~wrong~~**right** */}
      <style>{`
        .pb-response del {
          color: var(--danger);
          background: rgba(179, 59, 46, 0.1);
          text-decoration: line-through;
          padding: 1px 3px;
          border-radius: var(--radius-xs);
        }
        .pb-response del + strong,
        .pb-response del + em {
          color: var(--success);
          background: rgba(47, 111, 85, 0.12);
          text-decoration: none;
          border-bottom: 2px solid rgba(47, 111, 85, 0.5);
          padding: 1px 3px;
          border-radius: var(--radius-xs);
          font-weight: 500;
          font-style: normal;
        }
      `}</style>

      {/* Messages / Welcome */}
      <div className="flex-1 overflow-y-auto p-4 min-h-[420px]">
        {isEmpty ? (
          <div style={{ maxWidth: 640, margin: '60px auto', textAlign: 'center' }}>
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
              Powered by Skill & RAG — chat with AI more effectively
              <br />
              基于 Skill、RAG，让你和 AI 聊天更加顺畅
            </p>

            {/* Quick prompts */}
            <div className="quick-prompt-grid">
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
                    borderRadius: 'var(--radius-sm)',
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
              Click an example to get started, or type your own request / 点击示例快速开始
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[85%] px-4 py-2 group relative ${m.role === 'assistant' ? 'pb-response' : ''}`}
                  style={{
                    borderRadius: 'var(--radius-sm)',
                    fontFamily: m.role === 'user' ? 'var(--mono)' : 'var(--serif)',
                    fontSize: m.role === 'user' ? 13 : 14,
                    background: m.role === 'user' ? 'var(--accent)' : 'var(--bg2)',
                    color: m.role === 'user' ? '#fff' : 'var(--dark)',
                    border: m.role === 'user' ? 'none' : '1px solid var(--line)',
                  }}
                >
                  {m.role === 'assistant' ? renderAssistantMessage(m.content) : m.content}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="flex items-center gap-2 p-3 chat-input-row" style={{ borderTop: '1px solid var(--line)' }}>
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
