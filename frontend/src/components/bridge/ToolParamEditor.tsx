/* Dynamic tool parameter editor — graptolite.ai style */

import type { ToolParam, ToolChoiceItem } from '../../types/api'

interface Props {
  params: ToolParam[]
  choices: Record<string, ToolChoiceItem[]>
  values: Record<string, string>
  onChange: (name: string, value: string) => void
}

export default function ToolParamEditor({ params, choices, values, onChange }: Props) {
  if (!params.length) {
    return <p style={{ fontFamily: 'var(--serif)', fontStyle: 'italic', fontSize: 14, color: 'var(--faint)' }}>Tool has no parameters -- click Run directly</p>
  }

  return (
    <div className="space-y-3">
      {params.map((p) => {
        const items = choices[p.name]
        const description = stripDefaultUnitNote(p.description)
        if (items && items.length > 0) {
          return (
            <div key={p.name}>
              <label className="label-text">
                {p.name}{description ? ` — ${description}` : ''}
              </label>
              <select
                className="input-field"
                value={values[p.name] ?? ''}
                onChange={e => onChange(p.name, e.target.value)}
              >
                <option value="">-- select --</option>
                {items.map((item, i) => (
                  <option key={i} value={item.value}>{item.label}</option>
                ))}
              </select>
            </div>
          )
        }
        return (
          <div key={p.name}>
            <label className="label-text">
              {p.name}{description ? ` — ${description}` : ''}
            </label>
            <input
              type="text"
              className="input-field"
              value={values[p.name] ?? (p.default != null ? String(p.default) : '')}
              onChange={e => onChange(p.name, e.target.value)}
              placeholder={p.default != null ? String(p.default) : ''}
            />
          </div>
        )
      })}
    </div>
  )
}

function stripDefaultUnitNote(text?: string): string {
  return (text || '')
    .replace(/\s*[（(]\s*mm\s*[）)]\s*/gi, '')
    .replace(/\bmillimetres?\b|\bmillimeters?\b|毫米/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}
