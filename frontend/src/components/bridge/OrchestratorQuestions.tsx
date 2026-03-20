/* Dynamic orchestrator questions — graptolite.ai style */

import type { OrchestratorQuestion } from '../../types/api'

interface Props {
  questions: OrchestratorQuestion[]
  answers: Record<string, string>
  onChange: (slot: string, value: string) => void
}

export default function OrchestratorQuestions({ questions, answers, onChange }: Props) {
  if (!questions.length) return null

  return (
    <div className="space-y-3">
      <h4 className="label-text" style={{ fontSize: 12 }}>LLM Parameter Analysis</h4>
      {questions.map((q) => (
        <div key={q.slot}>
          <label className="label-text">{q.text}</label>
          {q.allow_custom !== false ? (
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
          {q.allow_custom !== false && (
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
