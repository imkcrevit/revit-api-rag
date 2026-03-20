/* Dynamic orchestrator questions — graptolite.ai style */

import { useState } from 'react'
import type { OrchestratorQuestion } from '../../types/api'
import { bridgeApi } from '../../api/bridge'

interface Props {
  questions: OrchestratorQuestion[]
  answers: Record<string, string>
  onChange: (slot: string, value: string) => void
}

export default function OrchestratorQuestions({ questions, answers, onChange }: Props) {
  const [picking, setPicking] = useState<string | null>(null)

  if (!questions.length) return null

  const handlePick = async (slot: string) => {
    setPicking(slot)
    try {
      const res = await bridgeApi.triggerSelection()
      const el = res.elements?.[0]
      if (el) {
        const id = String(el.Id ?? el.id ?? '')
        const name = String(el.Name ?? el.name ?? '')
        const cat = String(el.Category ?? el.category ?? '')
        const display = `${cat}: ${name} (ID: ${id})`
        onChange(slot, id)
        // Store display text as a data attribute for showing
        const input = document.querySelector(`[data-pick-slot="${slot}"]`) as HTMLInputElement
        if (input) input.value = display
      }
    } catch (e: any) {
      console.error('Pick failed:', e)
    } finally {
      setPicking(null)
    }
  }

  return (
    <div className="space-y-3">
      <h4 className="label-text" style={{ fontSize: 12 }}>LLM Parameter Analysis</h4>
      {questions.map((q) => (
        <div key={q.slot}>
          <label className="label-text">{q.text}</label>

          {/* Pick mode: button to select element in Revit */}
          {q._pick_mode ? (
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                data-pick-slot={q.slot}
                className="input-field"
                value={answers[q.slot] ?? ''}
                onChange={e => onChange(q.slot, e.target.value)}
                placeholder="Element ID (or click button to pick in Revit)"
                style={{ flex: 1 }}
              />
              <button
                className="btn-secondary"
                onClick={() => handlePick(q.slot)}
                disabled={picking !== null}
                style={{
                  padding: '4px 12px',
                  fontSize: 12,
                  whiteSpace: 'nowrap',
                  cursor: picking ? 'wait' : 'pointer',
                }}
              >
                {picking === q.slot ? 'Selecting...' : 'Pick in Revit'}
              </button>
            </div>
          ) : q.allow_custom !== false ? (
            <input
              list={`dl-${q.slot}`}
              className="input-field"
              value={answers[q.slot] ?? ''}
              onChange={e => onChange(q.slot, e.target.value)}
              placeholder="Select or type custom value"
            />
          ) : (
            <select
              className="input-field"
              value={answers[q.slot] ?? ''}
              onChange={e => onChange(q.slot, e.target.value)}
            >
              <option value="">-- select --</option>
              {q.options.map((opt, i) => (
                <option key={i} value={opt}>{opt}</option>
              ))}
            </select>
          )}
          {!q._pick_mode && q.allow_custom !== false && (
            <datalist id={`dl-${q.slot}`}>
              {q.options.map((opt, i) => (
                <option key={i} value={opt} />
              ))}
            </datalist>
          )}
        </div>
      ))}
    </div>
  )
}
