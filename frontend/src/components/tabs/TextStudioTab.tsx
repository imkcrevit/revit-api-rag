/* Tab: TextStudio — multilingual translation & text refinement */

import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage } from '../../types/api'
import { useSettingsStore } from '../../store'

/* ── Languages ── */
const LANGUAGES: Record<string, string> = {
  auto: 'Auto-detect',
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
  fr: 'Français',
  de: 'Deutsch',
  es: 'Español',
  ru: 'Русский',
  pt: 'Português',
  it: 'Italiano',
  ar: 'العربية',
  th: 'ไทย',
  vi: 'Tiếng Việt',
}

/* ── Cost status type ── */
interface CostStatus {
  date: string
  cost_usd: number
  limit_usd: number
  requests: number
  interrupted: boolean
  remaining_usd: number
  experimental?: boolean
}

const copyBtnStyle: React.CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 10,
  padding: '3px 10px',
  background: 'var(--accent)',
  border: 'none',
  borderRadius: 2,
  cursor: 'pointer',
  color: '#fff',
  letterSpacing: '0.05em',
  textTransform: 'uppercase',
}

export default function TextStudioTab() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [sourceLang, setSourceLang] = useState('auto')
  const [targetLang, setTargetLang] = useState('en')
  const [costStatus, setCostStatus] = useState<CostStatus | null>(null)
  const [accepted, setAccepted] = useState(() => sessionStorage.getItem('ts_exp_accepted') === '1')
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const { sessionId } = useSettingsStore()

  /* Fetch cost status */
  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch('/api/text-studio/status')
      if (resp.ok) setCostStatus(await resp.json())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { fetchStatus() }, [fetchStatus])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /* Auto-resize textarea */
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }, [input])

  /* Swap languages */
  const handleSwap = () => {
    if (sourceLang === 'auto') return
    setSourceLang(targetLang)
    setTargetLang(sourceLang)
  }

  const send = useCallback(async (text?: string) => {
    const msg = (text ?? input).trim()
    if (!msg || streaming) return
    if (!text) setInput('')
    setMessages(prev => [...prev, { role: 'user', content: msg }])
    setStreaming(true)

    const abort = new AbortController()
    abortRef.current = abort

    try {
      const resp = await fetch('/api/text-studio/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: msg,
          session_id: sessionId,
          source_lang: sourceLang,
          target_lang: targetLang,
          accept_experimental: true,
        }),
        signal: abort.signal,
      })

      // Cost limit exceeded → 503
      if (resp.status === 503) {
        const data = await resp.json()
        setCostStatus(data.status)
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `**Service Interrupted** — ${data.message}`,
        }])
        setStreaming(false)
        return
      }

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
        setMessages(prev => [...prev, { role: 'assistant', content: '(No response)' }])
      }

      // Refresh cost status after each request
      fetchStatus()
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${e.message}` }])
      }
    } finally {
      setStreaming(false)
    }
  }, [input, streaming, sessionId, sourceLang, targetLang, fetchStatus])

  const handleClear = () => {
    setMessages([])
    fetch('/api/text-studio/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    }).catch(() => {})
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).catch(() => {})
  }

  const renderMarkdown = (text: string) => (
    <ReactMarkdown remarkPlugins={[remarkGfm]}
      components={{
        code({ children, className }) {
          const isBlock = className?.includes('language-')
          if (!isBlock) {
            return <code style={{
              fontFamily: 'var(--mono)', fontSize: 12,
              background: 'var(--bg3)', padding: '1px 4px', borderRadius: 2,
            }}>{children}</code>
          }
          const codeText = String(children).replace(/\n$/, '')
          return (
            <div style={{ position: 'relative', margin: '8px 0' }}>
              <pre style={{
                fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--bg3)',
                border: '1px solid var(--line)', borderRadius: 2,
                padding: '12px 14px', overflowX: 'auto', whiteSpace: 'pre-wrap', lineHeight: 1.6,
              }}><code>{codeText}</code></pre>
              <button onClick={() => copyToClipboard(codeText)}
                style={{ ...copyBtnStyle, position: 'absolute', top: 6, right: 6 }}>Copy</button>
            </div>
          )
        },
      }}
    >{text}</ReactMarkdown>
  )

  const isEmpty = messages.length === 0
  const isInterrupted = costStatus?.interrupted === true
  const isExperimental = costStatus?.experimental === true

  /* Experimental gate — must click to enter */
  if (isExperimental && !accepted) {
    return (
      <div className="flex flex-col h-full" style={{ alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ maxWidth: 480, textAlign: 'center', padding: 40 }}>
          <div style={{
            display: 'inline-block', padding: '3px 12px', marginBottom: 16,
            fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 700,
            letterSpacing: '0.08em', textTransform: 'uppercase',
            background: 'rgba(230, 126, 34, 0.1)', color: '#e67e22',
            border: '1px solid rgba(230, 126, 34, 0.25)', borderRadius: 2,
          }}>
            Experimental
          </div>
          <h2 style={{
            fontFamily: 'var(--display)', fontSize: 20,
            letterSpacing: '0.1em', textTransform: 'uppercase',
            color: 'var(--dark)', marginBottom: 12,
          }}>
            TextStudio
          </h2>
          <p style={{
            fontFamily: 'var(--serif)', fontSize: 14,
            color: 'var(--mid)', lineHeight: 1.7, marginBottom: 8,
          }}>
            Multilingual translation & text refinement powered by DeepSeek.
          </p>
          <p style={{
            fontFamily: 'var(--serif)', fontSize: 13,
            color: 'var(--faint)', lineHeight: 1.6, marginBottom: 28,
          }}>
            This module is currently in testing. Daily usage is capped at $1.00.
            Service will be interrupted automatically if the limit is exceeded.
          </p>
          <button
            className="btn-primary"
            onClick={() => {
              sessionStorage.setItem('ts_exp_accepted', '1')
              setAccepted(true)
            }}
            style={{ fontSize: 12, padding: '10px 28px' }}
          >
            Enter Testing Mode
          </button>
          <p style={{
            fontFamily: 'var(--mono)', fontSize: 10,
            color: 'var(--faint)', marginTop: 16,
          }}>
            Your session will be recorded for quality monitoring.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Interrupted banner */}
      {isInterrupted && (
        <div style={{
          padding: '10px 20px',
          background: 'rgba(231, 76, 60, 0.08)',
          borderBottom: '1px solid rgba(231, 76, 60, 0.2)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
        }}>
          <span style={{
            display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
            background: '#e74c3c', flexShrink: 0,
          }} />
          <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: '#c0392b' }}>
            SERVICE INTERRUPTED — Daily cost limit (${costStatus?.limit_usd.toFixed(2)}) exceeded.
            Today: ${costStatus?.cost_usd.toFixed(4)} / {costStatus?.requests} requests.
            Awaiting manual reset.
          </span>
        </div>
      )}

      {/* Language selector bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '10px 20px',
        borderBottom: '1px solid var(--line)',
        background: 'var(--bg2)',
      }}>
        {isExperimental && (
          <span style={{
            fontFamily: 'var(--mono)', fontSize: 9, fontWeight: 700,
            padding: '2px 8px', borderRadius: 2, marginRight: 4,
            background: 'rgba(230, 126, 34, 0.1)', color: '#e67e22',
            letterSpacing: '0.06em', textTransform: 'uppercase',
          }}>
            EXP
          </span>
        )}
        <select
          value={sourceLang}
          onChange={e => setSourceLang(e.target.value)}
          style={selectStyle}
        >
          {Object.entries(LANGUAGES).map(([code, label]) => (
            <option key={code} value={code}>{label}</option>
          ))}
        </select>

        <button
          onClick={handleSwap}
          disabled={sourceLang === 'auto'}
          title="Swap languages"
          style={{
            fontFamily: 'var(--mono)', fontSize: 14,
            padding: '4px 10px', border: '1px solid var(--line)',
            background: 'var(--bg)', borderRadius: 2, cursor: sourceLang === 'auto' ? 'default' : 'pointer',
            color: sourceLang === 'auto' ? 'var(--faint)' : 'var(--dark)',
            transition: 'all .15s',
          }}
        >
          &#8644;
        </button>

        <select
          value={targetLang}
          onChange={e => setTargetLang(e.target.value)}
          style={selectStyle}
        >
          {Object.entries(LANGUAGES).filter(([code]) => code !== 'auto').map(([code, label]) => (
            <option key={code} value={code}>{label}</option>
          ))}
        </select>

        {/* Cost indicator */}
        {costStatus && (
          <div style={{
            marginLeft: 'auto',
            fontFamily: 'var(--mono)', fontSize: 10,
            color: isInterrupted ? '#e74c3c' : 'var(--faint)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{
              display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
              background: isInterrupted ? '#e74c3c' : '#27ae60',
            }} />
            ${costStatus.cost_usd.toFixed(4)} / ${costStatus.limit_usd.toFixed(2)}
          </div>
        )}
      </div>

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
              TextStudio
            </h2>
            <p style={{
              fontFamily: 'var(--serif)',
              fontSize: 15,
              color: 'var(--mid)',
              marginBottom: 32,
              lineHeight: 1.6,
            }}>
              Multilingual translation & text refinement
              <br />
              多语言翻译 &middot; 文本润色 &middot; 语法检查
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
                  onClick={() => {
                    if (q.source) setSourceLang(q.source)
                    if (q.target) setTargetLang(q.target)
                    setInput(prev => prev + q.text)
                  }}
                  disabled={streaming || isInterrupted}
                  style={{
                    fontFamily: 'var(--mono)',
                    fontSize: 12,
                    padding: '14px 12px',
                    background: 'var(--bg)',
                    border: '1px solid var(--subtle)',
                    borderRadius: 2,
                    cursor: isInterrupted ? 'not-allowed' : 'pointer',
                    textAlign: 'left',
                    transition: 'all 0.2s',
                    color: 'var(--dark)',
                    opacity: isInterrupted ? 0.5 : 1,
                  }}
                  onMouseEnter={e => {
                    if (!isInterrupted) {
                      e.currentTarget.style.borderColor = 'var(--accent)'
                      e.currentTarget.style.color = 'var(--accent)'
                    }
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.borderColor = 'var(--subtle)'
                    e.currentTarget.style.color = 'var(--dark)'
                  }}
                >
                  <div style={{ fontWeight: 500 }}>{q.label}</div>
                  <div style={{ fontSize: 11, color: 'var(--faint)', marginTop: 4 }}>{q.hint}</div>
                </button>
              ))}
            </div>

            <p style={{
              fontFamily: 'var(--mono)',
              fontSize: 11,
              color: 'var(--faint)',
              letterSpacing: '0.03em',
            }}>
              Select languages above, then paste your text
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
                    whiteSpace: m.role === 'user' ? 'pre-wrap' : undefined,
                  }}
                >
                  {m.role === 'assistant' ? (
                    <>
                      {renderMarkdown(m.content)}
                      <button
                        onClick={() => copyToClipboard(m.content)}
                        className="opacity-0 group-hover:opacity-100 transition-opacity"
                        style={{
                          ...copyBtnStyle,
                          position: 'absolute',
                          top: 6,
                          right: 6,
                          background: 'var(--mid)',
                        }}
                      >
                        Copy All
                      </button>
                    </>
                  ) : m.content}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="flex items-end gap-2 p-3" style={{ borderTop: '1px solid var(--line)' }}>
        <textarea
          ref={textareaRef}
          className="input-field flex-1"
          placeholder={isInterrupted
            ? 'Service interrupted — daily cost limit exceeded'
            : 'Paste text to translate or polish...'}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          disabled={streaming || isInterrupted}
          rows={1}
          style={{
            resize: 'none', minHeight: 38, maxHeight: 200, lineHeight: 1.5,
            opacity: isInterrupted ? 0.5 : 1,
          }}
        />
        <button onClick={() => send()} disabled={streaming || isInterrupted} className="btn-primary"
          style={{ alignSelf: 'flex-end', marginBottom: 1 }}>
          {isInterrupted ? 'Interrupted' : 'Translate'}
        </button>
        <button onClick={handleClear} className="btn-secondary"
          style={{ alignSelf: 'flex-end', marginBottom: 1 }}>
          Clear
        </button>
      </div>
    </div>
  )
}

/* ── Quick prompts ── */
const QUICK_PROMPTS = [
  { label: '中 → English', hint: 'Chinese to English', text: '', source: 'zh', target: 'en' },
  { label: 'En → 中文', hint: 'English to Chinese', text: '', source: 'en', target: 'zh' },
  { label: '中 → 日本語', hint: 'Chinese to Japanese', text: '', source: 'zh', target: 'ja' },
  { label: '中文润色', hint: 'Polish Chinese text', text: '请帮我润色以下段落：', source: 'zh', target: 'zh' },
  { label: '语法检查', hint: 'Check grammar', text: '请检查以下文本的语法和用词问题：', source: 'auto', target: '' },
  { label: 'Formal Rewrite', hint: 'Rewrite formally', text: 'Please rewrite the following text in a formal tone:\n', source: 'auto', target: 'en' },
]

/* ── Shared styles ── */
const selectStyle: React.CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 12,
  padding: '6px 12px',
  border: '1px solid var(--line)',
  background: 'var(--bg)',
  borderRadius: 2,
  color: 'var(--dark)',
  cursor: 'pointer',
  minWidth: 130,
}
