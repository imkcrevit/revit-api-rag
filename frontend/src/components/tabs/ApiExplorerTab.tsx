/* Tab C: API Explorer — graptolite.ai style */

import { useState } from 'react'
import { bridgeApi } from '../../api/bridge'
import type { ApiSearchResult, SdkSearchResult } from '../../types/api'
import Accordion from '../shared/Accordion'
import { getErrorMessage } from '../../utils/errors'

export default function ApiExplorerTab() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<'fast' | 'full'>('full')
  const [topK, setTopK] = useState(15)
  const [status, setStatus] = useState('')
  const [apiItems, setApiItems] = useState<ApiSearchResult[]>([])
  const [sdkItems, setSdkItems] = useState<SdkSearchResult[]>([])
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)
  const [generatedCode, setGeneratedCode] = useState('')
  const [hint, setHint] = useState('')
  const [searching, setSearching] = useState(false)
  const [generating, setGenerating] = useState(false)

  const search = async () => {
    if (!query.trim() || searching) return
    setSearching(true)
    setSelectedIdx(null)
    setGeneratedCode('')
    try {
      const t0 = performance.now()
      const result = await bridgeApi.apiSearch(query.trim(), topK, mode === 'fast')
      const elapsed = ((performance.now() - t0) / 1000).toFixed(1)
      const items = (result.api_items || []) as ApiSearchResult[]
      const sdk = (result.sdk_items || []) as SdkSearchResult[]
      setApiItems(items)
      setSdkItems(sdk)
      let s = `Found ${items.length} API docs, ${sdk.length} SDK examples (${elapsed}s)`
      if (result.rewritten_query !== query) s += `\nRewritten: ${result.rewritten_query}`
      setStatus(s)
    } catch (e: unknown) {
      setStatus(`Error: ${getErrorMessage(e)}`)
    } finally {
      setSearching(false)
    }
  }

  const selected = selectedIdx !== null ? apiItems[selectedIdx] : null

  const detailText = selected ? [
    selected.full_id ? `// ${selected.full_id}` : '',
    selected.syntax || '',
    selected.parameters ? `\n// Parameters:\n// ${selected.parameters}` : '',
    selected.remark ? `\n// Remark:\n// ${selected.remark}` : '',
  ].filter(Boolean).join('\n') : ''

  const generate = async () => {
    if (!selected || generating) return
    setGenerating(true)
    try {
      const result = await bridgeApi.apiCodegen(
        selected.full_id || selected.name,
        detailText,
        hint,
      )
      setGeneratedCode(result.code || '// No code generated')
    } catch (e: unknown) {
      setGeneratedCode(`// Error: ${getErrorMessage(e)}`)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="p-4 space-y-4">
      <h3 className="heading-display" style={{ fontSize: 16 }}>
        Revit API Explorer
      </h3>
      <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
        Search &rarr; Rerank &rarr; Generate
      </p>

      {/* Search */}
      <div className="flex gap-2 query-action-row">
        <input
          className="input-field flex-1"
          placeholder="Type API keyword: Part, Wall.Create, ..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && search()}
        />
        <button onClick={search} disabled={searching} className="btn-primary">
          {searching ? 'Searching...' : 'Search API'}
        </button>
      </div>

      <div className="flex gap-4 items-center" style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--mid)' }}>
        <label className="flex items-center gap-1 cursor-pointer">
          <input type="radio" checked={mode === 'fast'} onChange={() => setMode('fast')} />
          Fast (embedding only)
        </label>
        <label className="flex items-center gap-1 cursor-pointer">
          <input type="radio" checked={mode === 'full'} onChange={() => setMode('full')} />
          Full (rewrite + rerank)
        </label>
        <label className="flex items-center gap-1">
          Results:
          <input type="range" min={5} max={30} step={5} value={topK}
            onChange={e => setTopK(Number(e.target.value))} className="w-20" />
          {topK}
        </label>
      </div>

      {status && (
        <div style={{
          fontFamily: 'var(--mono)',
          fontSize: 12,
          color: 'var(--mid)',
          whiteSpace: 'pre-line',
          background: 'var(--bg2)',
          padding: '8px 12px',
          borderRadius: 2,
        }}>{status}</div>
      )}

      {/* Results table */}
      {apiItems.length > 0 && (
        <div className="card table-scroll">
          <table className="w-full" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
            <thead>
              <tr style={{ background: 'var(--bg2)' }}>
                <th className="px-3 py-2 text-left label-text w-10">#</th>
                <th className="px-3 py-2 text-left label-text">API Name</th>
                <th className="px-3 py-2 text-left label-text">Summary</th>
                <th className="px-3 py-2 text-left label-text w-20">Distance</th>
              </tr>
            </thead>
            <tbody>
              {apiItems.map((item, i) => (
                <tr
                  key={i}
                  onClick={() => { setSelectedIdx(i); setGeneratedCode('') }}
                  style={{
                    cursor: 'pointer',
                    background: selectedIdx === i ? 'rgba(217,119,87,0.08)' : 'transparent',
                    borderBottom: '1px solid var(--line)',
                  }}
                  onMouseEnter={e => { if (selectedIdx !== i) e.currentTarget.style.background = 'var(--bg2)' }}
                  onMouseLeave={e => { if (selectedIdx !== i) e.currentTarget.style.background = 'transparent' }}
                >
                  <td className="px-3 py-1.5">{i + 1}</td>
                  <td className="px-3 py-1.5" style={{ fontWeight: 500 }}>{item.name}</td>
                  <td className="px-3 py-1.5 truncate max-w-[300px]" style={{ fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--mid)' }}>{item.summary?.slice(0, 120)}</td>
                  <td className="px-3 py-1.5" style={{ color: 'var(--faint)' }}>{item.distance.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* SDK examples */}
      <Accordion title={`SDK Code Examples (${sdkItems.length})`}>
        {sdkItems.length === 0 ? (
          <p style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--mid)' }}>None</p>
        ) : (
          sdkItems.map((si, i) => {
            const apis = Array.isArray(si.mentioned_apis)
              ? si.mentioned_apis.join(', ')
              : si.mentioned_apis || ''
            return (
              <div key={i} className="mb-3" style={{ borderBottom: '1px solid var(--line)', paddingBottom: 8 }}>
                <p style={{ fontFamily: 'var(--mono)', fontSize: 12, fontWeight: 600, color: 'var(--dark)' }}>
                  {si.project}
                </p>
                {si.summary && (
                  <p style={{ fontSize: 12, color: 'var(--mid)', margin: '2px 0 4px' }}>{si.summary}</p>
                )}
                {apis && (
                  <p style={{ fontSize: 11, color: 'var(--mid)' }}>
                    <span style={{ fontWeight: 500 }}>Classes: </span>{apis}
                  </p>
                )}
                {si.content ? (
                  <pre style={{
                    background: 'var(--bg2)',
                    padding: 8,
                    borderRadius: 2,
                    fontFamily: 'var(--mono)',
                    fontSize: 12,
                    overflowX: 'auto',
                    marginTop: 4,
                  }}>{si.content}</pre>
                ) : (
                  <p style={{ fontSize: 11, color: 'var(--mid)', fontStyle: 'italic', marginTop: 4 }}>
                    (Code not available)
                  </p>
                )}
              </div>
            )
          })
        )}
      </Accordion>

      {/* Selected detail */}
      {selected && (
        <div className="space-y-3">
          <pre style={{
            background: 'var(--bg2)',
            padding: 12,
            borderRadius: 2,
            fontFamily: 'var(--mono)',
            fontSize: 12,
            overflowX: 'auto',
            border: '1px solid var(--line)',
          }}>{detailText}</pre>
          <div className="flex gap-2 query-action-row">
            <input
              className="input-field flex-1"
              placeholder="Hint (optional): e.g. create a wall between two points"
              value={hint}
              onChange={e => setHint(e.target.value)}
            />
            <button onClick={generate} disabled={generating} className="btn-primary">
              {generating ? 'Generating...' : 'Generate Code Example'}
            </button>
          </div>
        </div>
      )}

      {generatedCode && (
        <pre style={{
          background: 'var(--bg2)',
          color: 'var(--dark)',
          padding: 16,
          borderRadius: 2,
          fontFamily: 'var(--mono)',
          fontSize: 12,
          overflowX: 'auto',
        }}>{generatedCode}</pre>
      )}
    </div>
  )
}
