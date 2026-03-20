/* Tool Library — graptolite.ai style */

import { useState, useEffect, useCallback } from 'react'
import { bridgeApi } from '../../api/bridge'
import type { ToolInfo, ToolParam, ToolChoiceItem } from '../../types/api'
import StepIndicator from '../shared/StepIndicator'
import ToolParamEditor from './ToolParamEditor'
import Accordion from '../shared/Accordion'

const STEPS = ['Select Tool', 'Load Choices', 'Set Params', 'Run']

interface Props {
  autoSelectTool?: string
  /** Called when user wants to regenerate code instead of using this tool */
  onSkipToGenerate?: (query: string) => void
}

export default function ToolLibrary({ autoSelectTool, onSkipToGenerate }: Props) {
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [selected, setSelected] = useState('')
  const [step, setStep] = useState(1)
  const [params, setParams] = useState<ToolParam[]>([])
  const [choices, setChoices] = useState<Record<string, ToolChoiceItem[]>>({})
  const [values, setValues] = useState<Record<string, string>>({})
  const [result, setResult] = useState('')
  const [loading, setLoading] = useState(false)
  const [codeTemplate, setCodeTemplate] = useState('')
  const [showCode, setShowCode] = useState(false)
  const [toolDescription, setToolDescription] = useState('')
  const [sourceQuery, setSourceQuery] = useState('')

  const refresh = useCallback(async () => {
    try {
      const list = await bridgeApi.listTools()
      setTools(list)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Auto-select tool when matched from pipeline
  useEffect(() => {
    if (autoSelectTool && autoSelectTool !== selected) {
      setSelected(autoSelectTool)
      loadChoices(autoSelectTool)
    }
  }, [autoSelectTool])

  const loadChoices = async (name: string) => {
    if (!name) return
    setLoading(true)
    setStep(2)
    setShowCode(false)
    try {
      const detail = await bridgeApi.getToolDetail(name)
      const allParams = detail.parameters || []
      setParams(allParams)
      setCodeTemplate(detail.code_template || '')
      setToolDescription(detail.description || '')
      setSourceQuery((detail as any).source_query || '')

      const hasDynamic = allParams.some(p => p.choices_from)
      let ch: Record<string, ToolChoiceItem[]> = {}
      if (hasDynamic) {
        ch = await bridgeApi.getToolChoices(name)
      }
      setChoices(ch)

      const defaults: Record<string, string> = {}
      for (const p of allParams) {
        if (ch[p.name]?.length) {
          defaults[p.name] = ch[p.name][0].value
        } else if (p.default != null) {
          defaults[p.name] = String(p.default)
        }
      }
      setValues(defaults)
      setStep(3)
    } catch (e: any) {
      setResult(`Error loading: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  const run = async () => {
    if (!selected) return
    setLoading(true)
    setStep(4)
    try {
      const res = await bridgeApi.runTool(selected, values)
      if (res.success) {
        setResult(`Success\n${JSON.stringify(res.result, null, 2)}`)
      } else {
        setResult(`Failed: ${res.error}`)
      }
    } catch (e: any) {
      setResult(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <StepIndicator steps={STEPS} current={step} />

      <button onClick={refresh} className="btn-secondary">
        Refresh Tools
      </button>

      {/* Tools table */}
      {tools.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
            <thead>
              <tr style={{ background: 'var(--bg2)' }}>
                <th className="px-3 py-2 text-left label-text">Name</th>
                <th className="px-3 py-2 text-left label-text">Description</th>
                <th className="px-3 py-2 text-left label-text w-16">Uses</th>
                <th className="px-3 py-2 text-left label-text">Tags</th>
              </tr>
            </thead>
            <tbody>
              {tools.map(t => (
                <tr
                  key={t.name}
                  onClick={() => { setSelected(t.name); setStep(1); setShowCode(false); setCodeTemplate('') }}
                  style={{
                    cursor: 'pointer',
                    background: selected === t.name ? 'rgba(217,119,87,0.08)' : 'transparent',
                    borderBottom: '1px solid var(--line)',
                  }}
                  onMouseEnter={e => { if (selected !== t.name) e.currentTarget.style.background = 'var(--bg2)' }}
                  onMouseLeave={e => { if (selected !== t.name) e.currentTarget.style.background = 'transparent' }}
                >
                  <td className="px-3 py-1.5" style={{ fontWeight: 500 }}>{t.name}</td>
                  <td className="px-3 py-1.5" style={{ fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--mid)' }}>{t.description}</td>
                  <td className="px-3 py-1.5">{t.execution_count}</td>
                  <td className="px-3 py-1.5" style={{ color: 'var(--faint)' }}>{t.tags?.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Selected tool actions */}
      {selected && (
        <div className="space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="label-text" style={{ margin: 0 }}>Selected:</span>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--accent)' }}>{selected}</span>
            <button
              onClick={() => loadChoices(selected)}
              disabled={loading}
              className="btn-primary"
            >
              {loading ? 'Loading...' : 'Load Parameters'}
            </button>
            {codeTemplate && (
              <button
                onClick={() => setShowCode(!showCode)}
                className="btn-secondary"
              >
                {showCode ? 'Hide Code' : 'View Code'}
              </button>
            )}
            {onSkipToGenerate && (
              <button
                onClick={() => onSkipToGenerate(sourceQuery || '')}
                className="btn-ghost"
                title="Skip this tool and generate new code via the full pipeline"
              >
                Regenerate Code Instead
              </button>
            )}
          </div>

          {/* Tool description */}
          {toolDescription && step >= 3 && (
            <p style={{ fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--mid)', fontStyle: 'italic', margin: 0 }}>
              {toolDescription}
            </p>
          )}
        </div>
      )}

      {/* Code template viewer */}
      {showCode && codeTemplate && (
        <Accordion title="Tool Code Template" defaultOpen>
          <textarea
            readOnly
            className="w-full min-h-[200px] p-4"
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 12,
              background: 'var(--bg2)',
              color: 'var(--dark)',
              border: '1px solid var(--line)',
              borderRadius: 2,
              resize: 'vertical',
            }}
            value={codeTemplate}
          />
          <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginTop: 4 }}>
            Placeholders like {'{{param_name}}'} are filled with parameter values at runtime.
          </p>
        </Accordion>
      )}

      {/* Params */}
      {step >= 3 && (
        <ToolParamEditor
          params={params}
          choices={choices}
          values={values}
          onChange={(name, value) => setValues(prev => ({ ...prev, [name]: value }))}
        />
      )}

      {/* Run */}
      {step >= 3 && (
        <button onClick={run} disabled={loading} className="btn-primary">
          {loading ? 'Running...' : 'Run Tool'}
        </button>
      )}

      {result && (
        <pre style={{
          background: 'var(--bg2)',
          border: '1px solid var(--line)',
          padding: 12,
          borderRadius: 2,
          fontFamily: 'var(--mono)',
          fontSize: 12,
          whiteSpace: 'pre-wrap',
        }}>{result}</pre>
      )}
    </div>
  )
}
