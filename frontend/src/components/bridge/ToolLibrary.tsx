/* Tool Library — graptolite.ai style */

import { useState, useEffect, useCallback } from 'react'
import { bridgeApi } from '../../api/bridge'
import type { ToolInfo, ToolParam, ToolChoiceItem } from '../../types/api'
import StepIndicator from '../shared/StepIndicator'
import ToolParamEditor from './ToolParamEditor'
import Accordion from '../shared/Accordion'
import { getErrorMessage } from '../../utils/errors'

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
  const [displayName, setDisplayName] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [editMode, setEditMode] = useState(false)
  const [editDisplayName, setEditDisplayName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editSourceQuery, setEditSourceQuery] = useState('')
  const [editTags, setEditTags] = useState('')
  const [editParamsText, setEditParamsText] = useState('[]')
  const [editCode, setEditCode] = useState('')
  const [saveStatus, setSaveStatus] = useState('')
  const [reviewStatus, setReviewStatus] = useState('')
  const [choicesWarning, setChoicesWarning] = useState('')

  const refresh = useCallback(async () => {
    try {
      const list = await bridgeApi.listTools()
      setTools(list)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const loadChoices = useCallback(async (name: string) => {
    if (!name) return
    setLoading(true)
    setStep(2)
    setShowCode(false)
    setEditMode(false)
    setSaveStatus('')
    setReviewStatus('')
    setChoicesWarning('')
    setResult('')
    try {
      const detail = await bridgeApi.getToolDetail(name)
      const allParams = detail.parameters || []
      setParams(allParams)
      setCodeTemplate(detail.code_template || '')
      setToolDescription(detail.description || '')
      setSourceQuery(detail.source_query || '')
      setDisplayName(detail.display_name || detail.name)
      setTags(detail.tags || [])
      setEditDisplayName(detail.display_name || detail.name)
      setEditDescription(detail.description || '')
      setEditSourceQuery(detail.source_query || '')
      setEditTags((detail.tags || []).join(', '))
      setEditParamsText(JSON.stringify(allParams, null, 2))
      setEditCode(detail.code_template || '')

      const hasDynamic = allParams.some(p => p.choices_from)
      let ch: Record<string, ToolChoiceItem[]> = {}
      if (hasDynamic) {
        try {
          ch = await bridgeApi.getToolChoices(name)
        } catch (e: unknown) {
          setChoicesWarning(`Dynamic Revit choices unavailable: ${getErrorMessage(e)}. You can still view code and fill parameters manually.`)
        }
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
    } catch (e: unknown) {
      setResult(`Error loading: ${getErrorMessage(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  const beginEdit = async () => {
    if (!selected) return
    if (!codeTemplate) {
      await loadChoices(selected)
    }
    setShowCode(true)
    setEditMode(true)
    setSaveStatus('')
    setReviewStatus('')
  }

  const reviewEditedCode = async () => {
    setReviewStatus('Reviewing...')
    try {
      const res = await bridgeApi.reviewCode(editCode)
      setReviewStatus(res.safe
        ? 'Static review: safe'
        : `Static review warning: ${res.warnings.join('; ')}`)
    } catch (e: unknown) {
      setReviewStatus(`Review error: ${getErrorMessage(e)}`)
    }
  }

  const saveTool = async () => {
    if (!selected) return
    let parsedParams: ToolParam[]
    try {
      const parsed = JSON.parse(editParamsText)
      if (!Array.isArray(parsed)) throw new Error('Parameters must be an array')
      parsedParams = parsed
    } catch (e: unknown) {
      setSaveStatus(`Parameter JSON error: ${getErrorMessage(e)}`)
      return
    }

    setLoading(true)
    setSaveStatus('Saving...')
    try {
      const updated = await bridgeApi.updateTool(selected, {
        display_name: editDisplayName,
        description: editDescription,
        source_query: editSourceQuery,
        tags: editTags.split(',').map(t => t.trim()).filter(Boolean),
        parameters: parsedParams,
        code_template: editCode,
      })
      setCodeTemplate(updated.code_template || editCode)
      setToolDescription(updated.description || '')
      setSourceQuery(updated.source_query || '')
      setDisplayName(updated.display_name || updated.name)
      setTags(updated.tags || [])
      setParams(updated.parameters || [])
      setEditMode(false)
      setSaveStatus('Saved after static review')
      await refresh()
    } catch (e: unknown) {
      setSaveStatus(`Save error: ${getErrorMessage(e)}`)
    } finally {
      setLoading(false)
    }
  }

  // Auto-select tool when matched from pipeline
  useEffect(() => {
    if (autoSelectTool && autoSelectTool !== selected) {
      setSelected(autoSelectTool)
      loadChoices(autoSelectTool)
    }
  }, [autoSelectTool, loadChoices, selected])

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
    } catch (e: unknown) {
      setResult(`Error: ${getErrorMessage(e)}`)
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
        <div className="card table-scroll">
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
                  onClick={() => {
                    setSelected(t.name)
                    setStep(1)
                    setShowCode(false)
                    setEditMode(false)
                    setCodeTemplate('')
                    setDisplayName(t.display_name || t.name)
                    setToolDescription(t.description || '')
                    setTags(t.tags || [])
                    setSaveStatus('')
                    setReviewStatus('')
                    setChoicesWarning('')
                  }}
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
            <span style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--accent)' }}>{displayName || selected}</span>
            <button
              onClick={() => loadChoices(selected)}
              disabled={loading}
              className="btn-primary"
            >
              {loading ? 'Loading...' : 'Load Tool'}
            </button>
            {codeTemplate && (
              <button
                onClick={() => setShowCode(!showCode)}
                className="btn-secondary"
              >
                {showCode ? 'Hide Code' : 'View Code'}
              </button>
            )}
            <button
              onClick={beginEdit}
              disabled={loading}
              className="btn-secondary"
            >
              Edit Tool
            </button>
            {onSkipToGenerate && (
              <button
                onClick={() => onSkipToGenerate(sourceQuery || '')}
                className="btn-ghost"
              >
                Generate New Code
              </button>
            )}
          </div>

          {/* Tool description */}
          {toolDescription && step >= 3 && (
            <p style={{ fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--mid)', margin: 0 }}>
              {toolDescription}
            </p>
          )}
          {tags.length > 0 && step >= 3 && (
            <div className="tool-tag-row">
              {tags.map(tag => <span key={tag}>{tag}</span>)}
            </div>
          )}
          {choicesWarning && (
            <p className="tool-warning-status">{choicesWarning}</p>
          )}
        </div>
      )}

      {/* Code template viewer */}
      {showCode && codeTemplate && (
        <Accordion title={editMode ? 'Edit Tool' : 'Tool Code'} defaultOpen>
          {editMode && (
            <div className="tool-edit-grid">
              <label>
                <span className="label-text">Display Name</span>
                <input
                  className="input-field"
                  value={editDisplayName}
                  onChange={e => setEditDisplayName(e.target.value)}
                />
              </label>
              <label>
                <span className="label-text">Tags</span>
                <input
                  className="input-field"
                  value={editTags}
                  onChange={e => setEditTags(e.target.value)}
                />
              </label>
              <label className="tool-edit-wide">
                <span className="label-text">Description</span>
                <input
                  className="input-field"
                  value={editDescription}
                  onChange={e => setEditDescription(e.target.value)}
                />
              </label>
              <label className="tool-edit-wide">
                <span className="label-text">Source Query</span>
                <input
                  className="input-field"
                  value={editSourceQuery}
                  onChange={e => setEditSourceQuery(e.target.value)}
                />
              </label>
            </div>
          )}
          <textarea
            className="w-full min-h-[200px] p-4"
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 12,
              background: 'var(--bg2)',
              color: 'var(--dark)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--radius-sm)',
              resize: 'vertical',
            }}
            readOnly={!editMode}
            value={editMode ? editCode : codeTemplate}
            onChange={e => setEditCode(e.target.value)}
          />
          {editMode && (
            <label style={{ display: 'block', marginTop: 10 }}>
              <span className="label-text">Parameters JSON</span>
              <textarea
                className="w-full min-h-[120px] p-3"
                style={{
                  fontFamily: 'var(--mono)',
                  fontSize: 12,
                  background: 'var(--panel)',
                  color: 'var(--dark)',
                  border: '1px solid var(--line)',
                  borderRadius: 'var(--radius-sm)',
                  resize: 'vertical',
                }}
                value={editParamsText}
                onChange={e => setEditParamsText(e.target.value)}
              />
            </label>
          )}
          <div className="tool-edit-actions">
            {editMode ? (
              <>
                <button onClick={reviewEditedCode} className="btn-secondary" disabled={loading}>
                  Review Code
                </button>
                <button onClick={saveTool} className="btn-primary" disabled={loading}>
                  Save Tool
                </button>
                <button onClick={() => setEditMode(false)} className="btn-ghost" disabled={loading}>
                  Cancel
                </button>
              </>
            ) : (
              <span className="tool-code-note">Saved tool code is loaded directly for execution.</span>
            )}
          </div>
          {reviewStatus && <p className="tool-review-status">{reviewStatus}</p>}
          {saveStatus && <p className="tool-review-status">{saveStatus}</p>}
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
          borderRadius: 'var(--radius-sm)',
          fontFamily: 'var(--mono)',
          fontSize: 12,
          whiteSpace: 'pre-wrap',
        }}>{result}</pre>
      )}
    </div>
  )
}
