/* Tab: Skills — AI skill management, install, toggle, import from GitHub */

import { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/* ── Types ── */
interface Skill {
  id: string
  name: string
  description: string
  version: string
  author: string
  module: string
  enabled: boolean
  content?: string
  file_size?: number
  source?: string    // 'custom' | 'intent_bridge' | 'prompt_bridge'
  layer?: string     // 'base' | 'pattern' | 'workflow' | 'standard' | 'scenario'
  readonly?: boolean
  keywords?: string
}

/* ── Module label map ── */
const MODULE_LABELS: Record<string, string> = {
  global: 'All Modules',
  code_gen: 'Code Gen',
  prompt_bridge: 'PromptBridge',
  text_studio: 'TextStudio',
  mcp_bridge: 'MCP Bridge',
}

const MODULE_COLORS: Record<string, string> = {
  global: '#6b6860',
  code_gen: '#3498db',
  prompt_bridge: '#9b59b6',
  text_studio: '#27ae60',
  mcp_bridge: '#e67e22',
  intent_bridge: '#2980b9',
}

/* ── Source badge labels & colors ── */
const SOURCE_LABELS: Record<string, string> = {
  intent_bridge: 'Intent Bridge',
  prompt_bridge: 'PromptBridge',
  custom: 'Custom',
}

const SOURCE_COLORS: Record<string, string> = {
  intent_bridge: '#2c3e50',
  prompt_bridge: '#8e44ad',
  custom: '#27ae60',
}

const LAYER_LABELS: Record<string, string> = {
  base: 'Base',
  pattern: 'Pattern',
  workflow: 'Workflow',
  standard: 'Standard',
  scenario: 'Scenario',
}

/* ── Main component ── */
export default function SkillsTab() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [viewSkill, setViewSkill] = useState<Skill | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [showImport, setShowImport] = useState(false)

  const fetchSkills = useCallback(async () => {
    try {
      const resp = await fetch('/api/skills')
      if (!resp.ok) return
      const data = await resp.json()
      setSkills(data.skills)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchSkills() }, [fetchSkills])

  const handleToggle = async (id: string, enabled: boolean) => {
    // Built-in skills are always enabled — skip
    const skill = skills.find(s => s.id === id)
    if (skill?.readonly) return
    await fetch(`/api/skills/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    setSkills(prev => prev.map(s => s.id === id ? { ...s, enabled } : s))
  }

  const handleDelete = async (id: string) => {
    await fetch(`/api/skills/${id}`, { method: 'DELETE' })
    setSkills(prev => prev.filter(s => s.id !== id))
    setViewSkill(null)
  }

  const handleView = async (id: string) => {
    // encodeURIComponent handles compound IDs like "ib:patterns/point_based"
    const resp = await fetch(`/api/skills/${encodeURIComponent(id)}`)
    if (resp.ok) setViewSkill(await resp.json())
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px 28px', borderBottom: '1px solid var(--line)',
      }}>
        <div>
          <h2 style={{ fontFamily: 'var(--display)', fontSize: 18, letterSpacing: '.1em', textTransform: 'uppercase' }}>
            AI Skills
          </h2>
          <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginTop: 2 }}>
            {skills.filter(s => s.readonly).length} built-in &middot; {skills.filter(s => !s.readonly).length} custom &middot; {skills.filter(s => s.enabled).length} active
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-secondary" onClick={() => setShowImport(true)} style={{ fontSize: 11, padding: '8px 14px' }}>
            Import from GitHub
          </button>
          <button className="btn-primary" onClick={() => setShowAdd(true)} style={{ fontSize: 11, padding: '8px 14px' }}>
            + Add Skill
          </button>
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div style={{ textAlign: 'center', padding: 60, color: 'var(--faint)', fontFamily: 'var(--mono)', fontSize: 12 }}>Loading...</div>
        ) : skills.length === 0 ? (
          <EmptyState onAdd={() => setShowAdd(true)} onImport={() => setShowImport(true)} />
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
            {skills.map(s => (
              <SkillCard key={s.id} skill={s} onToggle={handleToggle} onView={handleView} />
            ))}
          </div>
        )}
      </div>

      {/* View modal */}
      {viewSkill && (
        <SkillViewModal skill={viewSkill} onClose={() => setViewSkill(null)} onDelete={handleDelete} />
      )}

      {/* Add modal */}
      {showAdd && (
        <SkillAddModal onClose={() => setShowAdd(false)} onSaved={() => { setShowAdd(false); fetchSkills() }} />
      )}

      {/* Import modal */}
      {showImport && (
        <SkillImportModal onClose={() => setShowImport(false)} onImported={() => { setShowImport(false); fetchSkills() }} />
      )}
    </div>
  )
}

/* ── Empty state ── */
function EmptyState({ onAdd, onImport }: { onAdd: () => void; onImport: () => void }) {
  return (
    <div style={{ maxWidth: 480, margin: '60px auto', textAlign: 'center' }}>
      <h3 style={{ fontFamily: 'var(--display)', fontSize: 16, letterSpacing: '.08em', textTransform: 'uppercase', marginBottom: 8 }}>
        No Skills Installed
      </h3>
      <p style={{ fontFamily: 'var(--serif)', fontSize: 15, color: 'var(--mid)', lineHeight: 1.6, marginBottom: 28 }}>
        Skills are AI behavioral protocols that enhance how modules respond.
        <br />
        Create your own or import from GitHub.
      </p>
      <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
        <button className="btn-primary" onClick={onAdd} style={{ fontSize: 11, padding: '10px 20px' }}>+ Create Skill</button>
        <button className="btn-secondary" onClick={onImport} style={{ fontSize: 11, padding: '10px 20px' }}>Import from GitHub</button>
      </div>
      <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginTop: 24 }}>
        Try: github.com/tanweai/pua
      </p>
    </div>
  )
}

/* ── Skill card ── */
function SkillCard({ skill, onToggle, onView }: {
  skill: Skill
  onToggle: (id: string, enabled: boolean) => void
  onView: (id: string) => void
}) {
  const modColor = MODULE_COLORS[skill.module] || 'var(--mid)'
  const srcColor = SOURCE_COLORS[skill.source || 'custom'] || 'var(--mid)'
  const isBuiltin = !!skill.readonly
  return (
    <div
      onClick={() => onView(skill.id)}
      style={{
        padding: '16px 18px',
        background: skill.enabled ? 'var(--bg)' : 'var(--bg2)',
        border: `1px solid ${skill.enabled ? 'var(--subtle)' : 'var(--line)'}`,
        borderRadius: 2,
        cursor: 'pointer',
        transition: 'all .15s',
        opacity: skill.enabled ? 1 : 0.6,
        position: 'relative',
      }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = skill.enabled ? 'var(--subtle)' : 'var(--line)' }}
    >
      {/* Toggle — hidden for read-only built-in skills */}
      {!isBuiltin && (
        <button
          onClick={e => { e.stopPropagation(); onToggle(skill.id, !skill.enabled) }}
          style={{
            position: 'absolute', top: 14, right: 14,
            width: 36, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer',
            background: skill.enabled ? 'var(--accent)' : 'var(--subtle)',
            transition: 'background .2s', padding: 0,
          }}
        >
          <div style={{
            width: 16, height: 16, borderRadius: '50%', background: '#fff',
            transform: skill.enabled ? 'translateX(18px)' : 'translateX(2px)',
            transition: 'transform .2s',
          }} />
        </button>
      )}

      {/* Built-in lock icon */}
      {isBuiltin && (
        <span style={{
          position: 'absolute', top: 14, right: 14,
          fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--faint)',
          padding: '2px 6px', border: '1px solid var(--line)', borderRadius: 2,
        }}>
          BUILT-IN
        </span>
      )}

      <div style={{ fontFamily: 'var(--display)', fontSize: 14, fontWeight: 500, letterSpacing: '.05em', marginBottom: 6, paddingRight: 70 }}>
        {skill.name}
      </div>

      <p style={{ fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--mid)', lineHeight: 1.5, marginBottom: 12, minHeight: 40 }}>
        {skill.description || 'No description'}
      </p>

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Source badge */}
        <span style={{
          fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 8px', borderRadius: 2,
          background: srcColor + '12', color: srcColor, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '.04em',
        }}>
          {SOURCE_LABELS[skill.source || 'custom'] || skill.source}
        </span>
        {/* Layer badge */}
        {skill.layer && (
          <span style={{
            fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 7px', borderRadius: 2,
            background: 'var(--line)', color: 'var(--mid)', textTransform: 'uppercase', fontWeight: 500, letterSpacing: '.03em',
          }}>
            {LAYER_LABELS[skill.layer] || skill.layer}
          </span>
        )}
        {/* Module badge */}
        <span style={{
          fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 8px', borderRadius: 2,
          background: modColor + '15', color: modColor, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '.04em',
        }}>
          {MODULE_LABELS[skill.module] || skill.module}
        </span>
        {skill.version && skill.version !== '-' && (
          <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)' }}>v{skill.version}</span>
        )}
        {skill.author && skill.author !== 'built-in' && (
          <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)' }}>by {skill.author}</span>
        )}
      </div>
    </div>
  )
}

/* ── View modal ── */
function SkillViewModal({ skill, onClose, onDelete }: {
  skill: Skill; onClose: () => void; onDelete: (id: string) => void
}) {
  const isBuiltin = !!skill.readonly
  const srcLabel = SOURCE_LABELS[skill.source || 'custom'] || skill.source || 'Custom'
  const layerLabel = skill.layer ? (LAYER_LABELS[skill.layer] || skill.layer) : ''
  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={{ ...modalStyle, maxWidth: 760 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
          <div>
            <h3 style={{ fontFamily: 'var(--display)', fontSize: 16, letterSpacing: '.08em', textTransform: 'uppercase' }}>{skill.name}</h3>
            <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginTop: 4 }}>
              {srcLabel}{layerLabel ? ` / ${layerLabel}` : ''} &middot; {skill.module}
              {skill.version && skill.version !== '-' ? ` · v${skill.version}` : ''}
              {skill.author && skill.author !== 'built-in' ? ` · ${skill.author}` : ''}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {!isBuiltin && (
              <button onClick={() => onDelete(skill.id)} style={{
                fontFamily: 'var(--mono)', fontSize: 10, padding: '4px 10px', border: '1px solid rgba(231,76,60,.3)',
                background: 'transparent', color: '#e74c3c', cursor: 'pointer', transition: 'all .15s',
              }}>Delete</button>
            )}
            <button onClick={onClose} style={{
              fontFamily: 'var(--mono)', fontSize: 10, padding: '4px 10px', border: '1px solid var(--subtle)',
              background: 'transparent', color: 'var(--mid)', cursor: 'pointer',
            }}>Close</button>
          </div>
        </div>

        {skill.description && (
          <p style={{ fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--mid)', marginBottom: 16, lineHeight: 1.5 }}>
            {skill.description}
          </p>
        )}

        {skill.keywords && (
          <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginBottom: 12 }}>
            Keywords: {skill.keywords}
          </p>
        )}

        <div style={{
          background: 'var(--bg2)', border: '1px solid var(--line)', borderRadius: 2,
          padding: '16px 20px', maxHeight: 500, overflow: 'auto',
          fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.7, color: 'var(--dark)',
        }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.content || '(empty)'}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

/* ── Add modal ── */
function SkillAddModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [author, setAuthor] = useState('')
  const [mod, setMod] = useState('global')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      const resp = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description: desc, author, module: mod, content }),
      })
      if (resp.ok) onSaved()
    } finally { setSaving(false) }
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={{ ...modalStyle, maxWidth: 680 }} onClick={e => e.stopPropagation()}>
        <h3 style={{ fontFamily: 'var(--display)', fontSize: 14, letterSpacing: '.1em', textTransform: 'uppercase', marginBottom: 16 }}>
          Create New Skill
        </h3>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div>
            <label style={labelStyle}>Name *</label>
            <input className="input-field" value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. code-reviewer" style={{ width: '100%' }} />
          </div>
          <div>
            <label style={labelStyle}>Author</label>
            <input className="input-field" value={author} onChange={e => setAuthor(e.target.value)}
              placeholder="Your name" style={{ width: '100%' }} />
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Description</label>
          <input className="input-field" value={desc} onChange={e => setDesc(e.target.value)}
            placeholder="What this skill does..." style={{ width: '100%' }} />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Target Module</label>
          <select className="input-field" value={mod} onChange={e => setMod(e.target.value)} style={{ width: '100%' }}>
            <option value="global">Global (All Modules)</option>
            <option value="code_gen">Code Generation</option>
            <option value="prompt_bridge">PromptBridge</option>
            <option value="text_studio">TextStudio</option>
            <option value="mcp_bridge">MCP Bridge</option>
          </select>
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={labelStyle}>Skill Content (Markdown)</label>
          <textarea
            className="input-field"
            value={content}
            onChange={e => setContent(e.target.value)}
            placeholder="# Behavioral Protocol&#10;&#10;Define how the AI should behave when this skill is active..."
            style={{ width: '100%', minHeight: 200, resize: 'vertical', lineHeight: 1.5, fontFamily: 'var(--mono)', fontSize: 12 }}
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleSave} disabled={saving || !name.trim()}>
            {saving ? 'Saving...' : 'Save Skill'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── Import modal ── */
function SkillImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [url, setUrl] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<Skill | null>(null)

  const handleImport = async () => {
    if (!url.trim()) return
    setImporting(true)
    setError('')
    try {
      const resp = await fetch('/api/skills/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      })
      if (!resp.ok) {
        const data = await resp.json()
        setError(data.detail || `Error ${resp.status}`)
        return
      }
      setResult(await resp.json())
    } catch (e: any) {
      setError(e.message)
    } finally {
      setImporting(false)
    }
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={{ ...modalStyle, maxWidth: 560 }} onClick={e => e.stopPropagation()}>
        <h3 style={{ fontFamily: 'var(--display)', fontSize: 14, letterSpacing: '.1em', textTransform: 'uppercase', marginBottom: 8 }}>
          Import from GitHub
        </h3>
        <p style={{ fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--mid)', marginBottom: 16, lineHeight: 1.5 }}>
          Paste a GitHub repository URL or direct link to a SKILL.md file.
        </p>

        {!result ? (
          <>
            <input
              className="input-field"
              value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleImport()}
              placeholder="https://github.com/tanweai/pua"
              style={{ width: '100%', marginBottom: 12 }}
            />

            <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)', marginBottom: 16, lineHeight: 1.6 }}>
              Supported formats:<br />
              &middot; github.com/user/repo (auto-detects SKILL.md)<br />
              &middot; github.com/user/repo/blob/main/path/to/file.md<br />
              &middot; raw.githubusercontent.com/user/repo/main/file.md
            </div>

            {error && (
              <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: '#c0392b', marginBottom: 12, padding: '8px 12px', background: 'rgba(231,76,60,.06)', border: '1px solid rgba(231,76,60,.15)', borderRadius: 2 }}>
                {error}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn-primary" onClick={handleImport} disabled={importing || !url.trim()}>
                {importing ? 'Importing...' : 'Import'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div style={{
              padding: 16, background: 'var(--bg2)', border: '1px solid var(--line)', borderRadius: 2, marginBottom: 16,
            }}>
              <div style={{ fontFamily: 'var(--display)', fontSize: 14, marginBottom: 4 }}>{result.name}</div>
              <div style={{ fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--mid)', marginBottom: 8 }}>
                {result.description || 'No description'}
              </div>
              <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)' }}>
                Module: {result.module} &middot; v{result.version} {result.author && `&middot; by ${result.author}`}
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn-primary" onClick={onImported}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/* ── Shared styles ── */
const overlayStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(20,20,19,0.4)', zIndex: 1000,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
}

const modalStyle: React.CSSProperties = {
  background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 2,
  padding: '24px 28px', width: '100%', maxHeight: '85vh', overflow: 'auto',
  boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
}

const labelStyle: React.CSSProperties = {
  fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)',
  textTransform: 'uppercase', letterSpacing: '.05em', display: 'block', marginBottom: 4,
}
