/* Tab D: MCP Bridge — 5-step code generation pipeline + tool library */
/* graptolite.ai style + tool match flow fix */

import { useState, useRef, useCallback } from 'react'
import { bridgeApi } from '../../api/bridge'
import { sseStream } from '../../api/client'
import { extractThinkingAndCode } from '../../utils/sse'
import type { ClassifyIntentResponse, OrchestratorQuestion, OrchestrateResponse, ToolParam } from '../../types/api'
import { getErrorMessage, isAbortError } from '../../utils/errors'
import StepIndicator from '../shared/StepIndicator'
import PipelineLog from '../shared/PipelineLog'
import ThinkingPanel from '../shared/ThinkingPanel'
import Accordion from '../shared/Accordion'
import OrchestratorQuestions from '../bridge/OrchestratorQuestions'
import ToolLibrary from '../bridge/ToolLibrary'

const STEPS = ['Input', 'Select', 'Review Code', 'Execute', 'Solidify']

/* --- Execution result renderer --- */
function ExecResultDisplay({ result }: { result: { ok: boolean; data: unknown; error?: string } }) {
  if (!result.ok) {
    return (
      <div style={{ marginTop: 8, padding: 12, background: 'rgba(217,119,87,0.08)', border: '1px solid var(--accent)', borderRadius: 2 }}>
        <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--accent)' }}>
          Failed: {result.error}
        </span>
      </div>
    )
  }

  const data = result.data
  if (!data) {
    return <p style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--mid)', marginTop: 8 }}>Success (no return data)</p>
  }

  // Flat object → key-value table
  if (typeof data === 'object' && !Array.isArray(data)) {
    const entries = Object.entries(data as Record<string, unknown>)
    return (
      <div style={{ marginTop: 8, border: '1px solid var(--line)', borderRadius: 2, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--mono)', fontSize: 12 }}>
          <tbody>
            {entries.map(([key, val]) => (
              <tr key={key} style={{ borderBottom: '1px solid var(--line)' }}>
                <td style={{ padding: '6px 12px', background: 'var(--bg2)', color: 'var(--mid)', fontWeight: 500, whiteSpace: 'nowrap', width: 1 }}>
                  {key}
                </td>
                <td style={{ padding: '6px 12px', color: 'var(--dark)' }}>
                  {renderValue(val)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  // Fallback: raw JSON
  return (
    <pre style={{ marginTop: 8, background: 'var(--bg2)', padding: 12, borderRadius: 2, fontFamily: 'var(--mono)', fontSize: 12, whiteSpace: 'pre-wrap', border: '1px solid var(--line)' }}>
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}

function renderValue(val: unknown): React.ReactNode {
  if (val === null || val === undefined) return <span style={{ color: 'var(--faint)', fontStyle: 'italic' }}>null</span>
  if (typeof val === 'boolean') return <span style={{ color: val ? 'var(--mid)' : 'var(--accent)' }}>{String(val)}</span>
  if (typeof val === 'number') return <span style={{ color: 'var(--dark)', fontWeight: 500 }}>{val}</span>
  if (typeof val === 'string') return <span>{val}</span>
  if (Array.isArray(val)) return <span>{JSON.stringify(val)}</span>
  if (typeof val === 'object') return <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{JSON.stringify(val, null, 2)}</pre>
  return <span>{String(val)}</span>
}

function buildThinkingChain(orch: OrchestrateResponse, questions: OrchestratorQuestion[]): string {
  const lines: string[] = []
  if (orch.intent && !orch.summary?.startsWith('Error')) {
    const oi = orch.intent as Record<string, unknown>
    lines.push(`**Workflow**: \`${String(oi.name || '')}\`${oi.display_name ? ` - ${String(oi.display_name)}` : ''}`)
    lines.push('**Mode**: generate reviewed C# after the required parameters are confirmed.')
  }
  if (orch.action_plan?.length) {
    lines.push(`**Action plan**: ${orch.action_plan.length} step${orch.action_plan.length === 1 ? '' : 's'}`)
    for (const ap of orch.action_plan) {
      lines.push(`- Step ${String(ap.step || '')}: **${String(ap.display_name || '')}** ${ap.api_method ? `(\`${String(ap.api_method)}\`)` : ''}`)
    }
  }
  if (questions.length) {
    lines.push(`**Dynamic parameters**: ${questions.length}`)
    for (const q of questions) {
      const source = q._pick_mode || q.enrich === 'host_pick'
        ? 'Revit pick'
        : q.enrich || 'manual'
      lines.push(`- \`${q.slot}\`: ${source}${q.options?.length ? `, ${q.options.length} choice${q.options.length === 1 ? '' : 's'}` : ''}`)
    }
  }
  if (orch.summary) lines.push(`**Summary**: ${orch.summary}`)
  return lines.join('\n')
}

/* ── Slot status type ── */
interface SlotInfo { status: string; connected_at?: number; requests?: number }
interface SlotsStatus { max_slots: number; connected: number; slots: Record<string, SlotInfo> }
interface ToolContext {
  name?: string
  display_name?: string
  description?: string
  code_template?: string
  parameters?: ToolParam[]
}

function extractCategoryList(queries: ClassifyIntentResponse['queries']): string[] {
  return queries.flatMap(q => {
    const categories = q.params.categoryList
    return Array.isArray(categories)
      ? categories.filter((category): category is string => typeof category === 'string')
      : []
  })
}

export default function BridgeTab() {
  // --- Header state ---
  const [revitStatus, setRevitStatus] = useState('Revit Disconnected')
  const [slotId, setSlotId] = useState<string>(() => sessionStorage.getItem('mcp_slot') || '')
  const [slotsStatus, setSlotsStatus] = useState<SlotsStatus | null>(null)

  /* Persist slot selection */
  const selectSlot = (id: string) => {
    setSlotId(id)
    if (id) sessionStorage.setItem('mcp_slot', id)
    else sessionStorage.removeItem('mcp_slot')
  }

  /* Fetch slot status */
  const refreshSlots = async () => {
    try {
      const resp = await fetch('/api/v1/bridge/slots')
      if (resp.ok) setSlotsStatus(await resp.json())
    } catch { /* ignore */ }
  }

  // --- Pipeline state ---
  const [step, setStep] = useState(1)
  const [query, setQuery] = useState('')
  const [pipelineLog, setPipelineLog] = useState<string[]>([])
  const [elapsed, setElapsed] = useState('')
  const [thinking, setThinking] = useState('')
  const [code, setCode] = useState('')
  const [securityStatus, setSecurityStatus] = useState('')
  const [ragContext, setRagContext] = useState<Record<string, unknown> | null>(null)
  const [execResult, setExecResult] = useState<{ ok: boolean; data: unknown; error?: string } | null>(null)
  const [solidifyResult, setSolidifyResult] = useState('')
  const [generating, setGenerating] = useState(false)
  const [executing, setExecuting] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startTimeRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

  // --- Selection state (all dynamic via orchestrator) ---
  const [selections, setSelections] = useState<Record<string, unknown>>({})
  const [selectOpen, setSelectOpen] = useState(false)
  const [statusMsg, setStatusMsg] = useState('')
  const [intentMeta, setIntentMeta] = useState<Record<string, unknown>>({})

  // --- Orchestrator state (dynamic!) ---
  const [orchQuestions, setOrchQuestions] = useState<OrchestratorQuestion[]>([])
  const [orchAnswers, setOrchAnswers] = useState<Record<string, string>>({})
  const [orchData, setOrchData] = useState<OrchestrateResponse | null>(null)

  // --- Tool match state ---
  const [matchedTool, setMatchedTool] = useState('')
  const [toolLibraryOpen, setToolLibraryOpen] = useState(false)
  const toolLibraryRef = useRef<HTMLDivElement>(null)

  // --- Solidify ---
  const [toolName, setToolName] = useState('')
  const [toolDesc, setToolDesc] = useState('')

  // --- Timer ---
  const startTimer = useCallback(() => {
    startTimeRef.current = performance.now()
    timerRef.current = setInterval(() => {
      setElapsed(`processing | ${((performance.now() - startTimeRef.current) / 1000).toFixed(1)}s`)
    }, 500)
  }, [])

  const stopTimer = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    setElapsed(`completed | ${((performance.now() - startTimeRef.current) / 1000).toFixed(1)}s`)
  }, [])

  // --- Init (no auto-connect — user clicks Refresh manually) ---

  // --- Reset ---
  const reset = () => {
    setStep(1); setPipelineLog([]); setElapsed('')
    setThinking(''); setCode(''); setSecurityStatus('')
    setRagContext(null); setExecResult(null); setSolidifyResult('')
    setSelections({}); setSelectOpen(false); setStatusMsg('')
    setIntentMeta({})
    setOrchQuestions([]); setOrchAnswers({}); setOrchData(null)
    setMatchedTool('')
    setToolLibraryOpen(false)
  }

  // --- SSE Stream helper ---
  const runStream = async (query: string, sels: Record<string, unknown>, toolContext?: ToolContext | null) => {
    const t0 = performance.now()
    const logs: string[] = []
    let fullBuf = ''
    let tokenCount = 0
    let lastYield = 0
    let finalCode = ''
    let finalThinking = ''

    const abort = new AbortController()
    abortRef.current = abort

    const body: Record<string, unknown> = { query, selections: sels, api_top_k: 15, code_top_k: 5 }
    if (toolContext) body.tool_context = toolContext

    try {
      for await (const evt of sseStream(
        '/api/v1/bridge/generate-stream',
        body,
        abort.signal
      )) {
        const el = ((performance.now() - t0) / 1000).toFixed(1)

        if (evt.event === 'progress') {
          try {
            const msg = `${JSON.parse(evt.data)} (${el}s)`
            logs.push(msg)
            setPipelineLog([...logs])
          } catch { /* skip */ }
        } else if (evt.event === 'token') {
          try {
            const token = JSON.parse(evt.data)
            fullBuf += token
            tokenCount++
            const now = performance.now()
            if (tokenCount > 1 && now - lastYield < 150) continue
            lastYield = now

            const { thinking: th, code: cd } = extractThinkingAndCode(fullBuf)
            finalThinking = th
            finalCode = cd
            setThinking(th ? `**Thinking:**\n\n${th}` : '*Waiting for LLM response...*')
            setCode(cd)

            const codeLines = cd ? cd.split('\n').length : 0
            const genLog = logs.filter(l => !l.startsWith('LLM generating'))
            genLog.push(`LLM generating... ${codeLines} lines, ${tokenCount} tokens (${el}s)`)
            setPipelineLog([...genLog])
          } catch { /* skip */ }
        } else if (evt.event === 'done') {
          try {
            const done = JSON.parse(evt.data)
            finalCode = done.code || finalCode
            setCode(finalCode)
            setSecurityStatus(done.safe ? 'Safe' : `Warning: ${done.warnings?.join('; ')}`)
            setRagContext(done.rag_context)
          } catch { /* skip */ }
        }
      }
    } catch (e: unknown) {
      if (!isAbortError(e)) {
        logs.push(`Error: ${getErrorMessage(e)}`)
        setPipelineLog([...logs])
      }
    }

    const el = ((performance.now() - t0) / 1000).toFixed(1)
    const finalLogs = logs.filter(l => !l.startsWith('LLM generating'))
    const codeLines = finalCode ? finalCode.split('\n').length : 0
    finalLogs.push(`LLM generation complete -- ${codeLines} lines (${el}s)`)
    finalLogs.push(`Code extracted & security reviewed`)
    setPipelineLog(finalLogs)
    setThinking(finalThinking ? `**Thinking:**\n\n${finalThinking}` : '')
    setStep(3)
  }

  // --- Skip matched tool & generate fresh code without saved-tool context ---
  const skipToolAndGenerate = async () => {
    setMatchedTool('')
    setToolLibraryOpen(false)
    setGenerating(true)
    startTimer()
    setThinking('')
    setPipelineLog(['Skipping saved tool, generating new code from RAG...'])

    try {
      // Step 1: Classify intent
      setPipelineLog(['Classifying intent...'])
      const intent = await bridgeApi.classifyIntent(query)
      let itype = intent.interaction_type

      // Step 2: Orchestrate
      setPipelineLog(prev => [...prev, 'LLM analyzing...'])
      const orch = await bridgeApi.orchestrate(query)
      setOrchData(orch)
      const questions = orch.questions || []
      setOrchQuestions(questions)

      if (itype === 'direct' && questions.length) {
        itype = 'select_family'
      }

      if (itype === 'direct') {
        setStep(2)
        await runStream(query, {})
      } else {
        setStep(2)
        setSelectOpen(true)

        const queries = intent.queries || []
        const cats = extractCategoryList(queries)
        const famLabel = queries[0]?.label || 'Family Type'
        setIntentMeta({ label: famLabel, categories: cats, interaction_type: itype })

        setThinking(buildThinkingChain(orch, questions))

        let sm = `Classification: ${famLabel}`
        if (cats.length) sm += ` (${cats.map(c => c.replace('OST_', '')).join(', ')})`
        if (questions.length) sm += `\nLLM analysis: ${questions.length} parameters to confirm`
        setStatusMsg(sm)
      }
    } catch (e: unknown) {
      setPipelineLog(prev => [...prev, `Error: ${getErrorMessage(e)}`])
    } finally {
      stopTimer()
      setGenerating(false)
    }
  }

  // --- Generate button ---
  const generate = async () => {
    if (!query.trim() || generating) return
    reset()
    setGenerating(true)
    startTimer()

    try {
      // Step 0: Check tool match
      setPipelineLog(['Checking tool library...'])
      const matched = await bridgeApi.matchTool(query)
      if (matched.matched && matched.name) {
        setMatchedTool(matched.name)
        setThinking(
          `**Tool application**: \`${matched.name}\` - ${matched.display_name}\n\n` +
          `**Mode**: saved tool code is loaded directly; no new code generation is required.\n\n` +
          (matched.description ? `**Description**: ${matched.description}\n\n` : '') +
          (matched.parameters?.length
            ? `**Parameters**: ${matched.parameters.map(p => p.name).join(', ')}\n\n`
            : '**Parameters**: none\n\n') +
          `**Uses**: ${matched.execution_count} time(s).`
        )
        // Auto-expand tool library and scroll to it
        setToolLibraryOpen(true)
        setStep(1)
        stopTimer()
        setGenerating(false)
        // Scroll to tool library after render
        setTimeout(() => {
          toolLibraryRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }, 100)
        return
      }

      // Step 1: Classify intent
      setPipelineLog(['Classifying intent...'])
      const intent = await bridgeApi.classifyIntent(query)
      let itype = intent.interaction_type

      // Step 2: Orchestrate
      setPipelineLog(prev => [...prev, 'LLM analyzing...'])
      const orch = await bridgeApi.orchestrate(query)
      setOrchData(orch)
      const questions = orch.questions || []
      setOrchQuestions(questions)

      // Override: if orchestrator has questions, force interactive
      if (itype === 'direct' && questions.length) {
        itype = 'select_family'
      }

      if (itype === 'direct') {
        setStep(2)
        await runStream(query, {})
      } else {
        // --- Interactive: all parameters come from orchestrator questions ---
        setStep(2)
        setSelectOpen(true)

        // Intent meta for code generation context
        const queries = intent.queries || []
        const cats = extractCategoryList(queries)
        const famLabel = queries[0]?.label || 'Family Type'
        setIntentMeta({ label: famLabel, categories: cats, interaction_type: itype })

        // Build thinking from orchestrator
        setThinking(buildThinkingChain(orch, questions))

        // Status
        let sm = `Classification: ${famLabel}`
        if (cats.length) sm += ` (${cats.map(c => c.replace('OST_', '')).join(', ')})`
        if (questions.length) sm += `\nLLM analysis: ${questions.length} parameters to confirm`
        setStatusMsg(sm)
      }
    } catch (e: unknown) {
      setPipelineLog(prev => [...prev, `Error: ${getErrorMessage(e)}`])
    } finally {
      stopTimer()
      setGenerating(false)
    }
  }

  // --- Confirm selections & generate ---
  const confirmSelection = async () => {
    setGenerating(true)
    startTimer()
    try {
      const sels: Record<string, unknown> = {}

      // All selections come from orchestrator answers
      const oa: Record<string, string> = {}
      for (const q of orchQuestions) {
        const val = orchAnswers[q.slot]
        if (!val) continue
        if (q.options.includes(val) && q.values?.length) {
          const idx = q.options.indexOf(val)
          oa[q.slot] = idx < q.values.length ? q.values[idx] : val
        } else {
          oa[q.slot] = val
        }
      }
      if (Object.keys(oa).length) sels._orchestrator_answers = oa
      if (orchData?.intent) sels._orchestrator_intent = orchData.intent

      // Pass classification meta
      if (intentMeta.categories) sels._revit_categories = intentMeta.categories
      if (intentMeta.label) sels._classification_label = intentMeta.label

      setSelections(sels)
      setSelectOpen(false)
      setStep(2)
      await runStream(query, sels)
    } catch (e: unknown) {
      setPipelineLog(prev => [...prev, `Error: ${getErrorMessage(e)}`])
    } finally {
      stopTimer()
      setGenerating(false)
    }
  }

  // --- Execute ---
  const execute = async () => {
    if (!code.trim()) return
    setExecuting(true)
    setStep(4)
    try {
      const res = await bridgeApi.execute(code)
      console.log('[execute] raw response:', JSON.stringify(res, null, 2))
      if (res.success) {
        // result may be: object, JSON string, plain string, or null
        let data: unknown = res.result
        if (typeof data === 'string') {
          try { data = JSON.parse(data) } catch { /* keep as string */ }
        }
        setExecResult({ ok: true, data: data ?? null })
      } else {
        setExecResult({ ok: false, data: null, error: res.error })
      }
    } catch (e: unknown) {
      setExecResult({ ok: false, data: null, error: getErrorMessage(e) })
    } finally {
      setExecuting(false)
    }
  }

  // --- Solidify ---
  const solidify = async () => {
    if (!toolName.trim() || !code.trim()) {
      setSolidifyResult('Please enter a tool name.')
      return
    }
    try {
      const res = await bridgeApi.solidify(toolName, code, toolDesc, query, thinking, selections)
      setSolidifyResult(`Solidified as '${res.name || toolName}'`)
      setStep(5)
    } catch (e: unknown) {
      setSolidifyResult(`Error: ${getErrorMessage(e)}`)
    }
  }

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2 bridge-status-row">
        <div className="flex-1 px-3 py-2" style={{
          background: 'var(--bg2)',
          border: '1px solid var(--line)',
          borderRadius: 2,
          fontFamily: 'var(--mono)',
          fontSize: 12,
          color: 'var(--mid)',
        }}>{revitStatus}</div>

        {/* Slot selector */}
        <select
          value={slotId}
          onChange={e => selectSlot(e.target.value)}
          style={{
            fontFamily: 'var(--mono)', fontSize: 11, padding: '6px 8px',
            border: '1px solid var(--line)', background: 'var(--bg)',
            borderRadius: 2, color: 'var(--dark)', cursor: 'pointer', minWidth: 110,
          }}
        >
          <option value="">TCP Direct</option>
          {Array.from({ length: slotsStatus?.max_slots ?? 5 }, (_, i) => {
            const sid = String(i + 1)
            const info = slotsStatus?.slots?.[sid]
            const connected = info?.status === 'connected'
            return (
              <option key={sid} value={sid}>
                Slot {sid} {connected ? `● (${info?.requests ?? 0} req)` : '○'}
              </option>
            )
          })}
        </select>

        <button onClick={() => {
          refreshSlots()
          bridgeApi.revitHealth().then(h => {
            if (h.revit_connected) {
              setRevitStatus(`Connected | ${h.latency_ms ? h.latency_ms + 'ms' : h.mode} | ${h.timestamp}`)
            } else {
              const wsInfo = h.ws_slots
              setRevitStatus(wsInfo?.connected
                ? `TCP offline, ${wsInfo.connected} WS slot(s) online`
                : `Disconnected: ${h.detail}`)
            }
          }).catch(() => setRevitStatus('Revit Disconnected: server unreachable'))
        }} className="btn-secondary">Connect</button>
      </div>

      {/* === Code Generation Pipeline === */}
      <h3 className="heading-display" style={{ fontSize: 16 }}>Code Generation Pipeline</h3>

      <div className="flex items-center gap-4">
        <div className="flex-1">
          <StepIndicator steps={STEPS} current={step} />
        </div>
        {elapsed && <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', fontStyle: 'italic' }}>{elapsed}</span>}
      </div>

      {/* Step 1: Input */}
      <div className="flex gap-2 query-action-row">
        <input
          className="input-field flex-1"
          placeholder="e.g. Create structural column at 100,0,0"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && generate()}
        />
        <button
          onClick={generate}
          disabled={generating}
          className="btn-primary"
        >
          {generating ? 'Generating...' : 'Generate Code'}
        </button>
      </div>

      {/* Tool Match Banner — shown when a solidified tool matches */}
      {matchedTool && (
        <div className="tool-match-banner">
          <div className="flex items-start justify-between gap-4 tool-match-content">
            <div className="flex-1">
              <h4 style={{
                fontFamily: 'var(--mono)',
                fontSize: 13,
                fontWeight: 600,
                color: 'var(--accent)',
                marginBottom: 8,
                letterSpacing: '0.03em',
              }}>
                Existing Tool Found
              </h4>
              <p style={{ fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--dark)', lineHeight: 1.5, margin: 0 }}>
                This request can run as a saved tool application. The saved tool code is loaded directly and can be reviewed or edited before execution.
              </p>
            </div>
            <div className="flex gap-2 shrink-0 tool-match-actions">
              <button
                onClick={() => {
                  setToolLibraryOpen(true)
                  setTimeout(() => {
                    toolLibraryRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }, 100)
                }}
                className="btn-primary"
              >
                Use Saved Tool
              </button>
              <button
                onClick={skipToolAndGenerate}
                disabled={generating}
                className="btn-secondary"
              >
                Generate New Code
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Process summary */}
      <ThinkingPanel content={thinking} title={matchedTool ? 'Tool Application' : 'Process'} />

      {/* Pipeline log */}
      <PipelineLog messages={pipelineLog} />

      {/* Step 2: Selections — fully dynamic from orchestrator */}
      <Accordion title="Step 2: Select Options" open={selectOpen} onToggle={setSelectOpen}>
        {statusMsg && (
          <div style={{
            fontFamily: 'var(--mono)',
            fontSize: 12,
            background: 'var(--bg2)',
            padding: '8px 12px',
            borderRadius: 2,
            marginBottom: 12,
            whiteSpace: 'pre-line',
            color: 'var(--mid)',
          }}>{statusMsg}</div>
        )}

        <OrchestratorQuestions
          questions={orchQuestions}
          answers={orchAnswers}
          onChange={(slot, val) => setOrchAnswers(prev => ({ ...prev, [slot]: val }))}
        />

        {orchQuestions.length === 0 && selectOpen && (
          <p style={{ fontFamily: 'var(--serif)', fontStyle: 'italic', fontSize: 14, color: 'var(--faint)' }}>
            No parameters to configure -- orchestrator did not generate questions.
          </p>
        )}

        <button
          onClick={confirmSelection}
          disabled={generating || orchQuestions.length === 0}
          className="btn-primary mt-3"
        >
          Confirm & Generate Code
        </button>
      </Accordion>

      {/* Step 3: Review Code */}
      <Accordion title="Step 3: Review Generated Code" defaultOpen>
        <textarea
          className="w-full min-h-[300px] p-4"
          style={{
            fontFamily: 'var(--mono)',
            fontSize: 12,
            background: 'var(--bg2)',
            color: 'var(--dark)',
            border: '1px solid var(--line)',
            borderRadius: 2,
            resize: 'vertical',
          }}
          value={code}
          onChange={e => setCode(e.target.value)}
        />
        {securityStatus && (
          <div style={{
            fontFamily: 'var(--mono)',
            fontSize: 12,
            marginTop: 8,
            color: securityStatus.startsWith('Safe') ? 'var(--mid)' : 'var(--accent)',
          }}>
            Security: {securityStatus}
          </div>
        )}
      </Accordion>

      {/* Step 4: Execute */}
      <Accordion title="Step 4: Execute" defaultOpen>
        <button
          onClick={execute}
          disabled={executing || !code.trim()}
          className="btn-primary"
        >
          {executing ? 'Executing...' : 'Execute in Revit'}
        </button>
        {execResult && <ExecResultDisplay result={execResult} />}
      </Accordion>

      {/* Step 5: Solidify — default collapsed */}
      <Accordion title="Step 5: Save as Reusable Tool">
        <div className="flex gap-2 mb-2 inline-form-row">
          <input className="input-field flex-1" placeholder="Tool name (e.g. create_wall)"
            value={toolName} onChange={e => setToolName(e.target.value)} />
          <input className="input-field flex-1" placeholder="Description"
            value={toolDesc} onChange={e => setToolDesc(e.target.value)} />
        </div>
        <button onClick={solidify} className="btn-primary">
          Solidify
        </button>
        {solidifyResult && <p style={{ fontFamily: 'var(--mono)', fontSize: 12, marginTop: 8, color: 'var(--mid)' }}>{solidifyResult}</p>}
      </Accordion>

      {/* RAG Context */}
      {ragContext && (
        <Accordion title="RAG Context">
          <pre style={{
            fontFamily: 'var(--mono)',
            fontSize: 12,
            background: 'var(--bg2)',
            padding: 12,
            borderRadius: 2,
            overflow: 'auto',
          }}>{JSON.stringify(ragContext, null, 2)}</pre>
        </Accordion>
      )}

      <hr style={{ border: 'none', borderTop: '1px solid var(--line)', margin: '24px 0' }} />

      {/* === Tool Library — default collapsed, auto-opens on match === */}
      <div ref={toolLibraryRef}>
        <Accordion
          title="Solidified Tool Library"
          open={toolLibraryOpen}
          onToggle={setToolLibraryOpen}
        >
          <ToolLibrary
            autoSelectTool={matchedTool}
            onSkipToGenerate={(q) => {
              if (q) setQuery(q)
              setMatchedTool('')
              setToolLibraryOpen(false)
              skipToolAndGenerate()
            }}
          />
        </Accordion>
      </div>
    </div>
  )
}
