/* Tab: Skills & Tools — AI skill management + solidified tool library */

import { useState, useEffect, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { bridgeApi } from '../../api/bridge'
import type { ToolInfo } from '../../types/api'
import { getErrorMessage } from '../../utils/errors'

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
  source?: string
  layer?: string
  readonly?: boolean
  keywords?: string
}

type ToolDetail = ToolInfo & { code_template?: string; source_query?: string }

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
  code_gen: '#164241',
  prompt_bridge: '#873a24',
  text_studio: '#2f6f55',
  mcp_bridge: '#9a5a16',
  intent_bridge: '#244b6a',
}

/* ── Source badge labels & colors ── */
const SOURCE_LABELS: Record<string, string> = {
  intent_bridge: 'Intent Bridge',
  prompt_bridge: 'PromptBridge',
  custom: 'Custom',
}

const SOURCE_COLORS: Record<string, string> = {
  intent_bridge: '#164241',
  prompt_bridge: '#873a24',
  custom: '#2f6f55',
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
  const [activeView, setActiveView] = useState<'skills' | 'tools'>('skills')
  const [skills, setSkills] = useState<Skill[]>([])
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [loadingSkills, setLoadingSkills] = useState(true)
  const [loadingTools, setLoadingTools] = useState(true)
  const [viewSkill, setViewSkill] = useState<Skill | null>(null)
  const [viewTool, setViewTool] = useState<ToolDetail | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [showImport, setShowImport] = useState(false)

  const skillsFetched = useRef(false)
  const toolsFetched = useRef(false)

  const fetchSkills = useCallback(async () => {
    setLoadingSkills(true)
    try {
      const resp = await fetch('/api/skills')
      if (!resp.ok) return
      const data = await resp.json()
      setSkills(data.skills)
      skillsFetched.current = true
    } catch { /* ignore */ }
    finally { setLoadingSkills(false) }
  }, [])

  const fetchTools = useCallback(async () => {
    setLoadingTools(true)
    try {
      const list = await bridgeApi.listTools()
      setTools(list)
      toolsFetched.current = true
    } catch { /* ignore */ }
    finally { setLoadingTools(false) }
  }, [])

  // Lazy load: only fetch when the sub-tab is first shown
  useEffect(() => {
    if (activeView === 'skills' && !skillsFetched.current) fetchSkills()
    if (activeView === 'tools' && !toolsFetched.current) fetchTools()
  }, [activeView, fetchSkills, fetchTools])

  const handleToggle = async (id: string, enabled: boolean) => {
    const skill = skills.find(s => s.id === id)
    if (skill?.readonly) return
    const resp = await fetch(`/api/skills/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!resp.ok) { alert(`Failed to update skill (${resp.status})`); return }
    setSkills(prev => prev.map(s => s.id === id ? { ...s, enabled } : s))
  }

  const handleDeleteSkill = async (id: string) => {
    const resp = await fetch(`/api/skills/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!resp.ok) { alert(`Failed to delete skill (${resp.status})`); return }
    setSkills(prev => prev.filter(s => s.id !== id))
    setViewSkill(null)
  }

  const handleDeleteTool = async (name: string) => {
    try {
      await bridgeApi.deleteTool(name)
      setTools(prev => prev.filter(t => t.name !== name))
      setViewTool(null)
    } catch { /* ignore */ }
  }

  const handleViewSkill = async (id: string) => {
    const resp = await fetch(`/api/skills/${encodeURIComponent(id)}`)
    if (resp.ok) setViewSkill(await resp.json())
  }

  const handleViewTool = async (name: string) => {
    try {
      const detail = await bridgeApi.getToolDetail(name)
      setViewTool(detail)
    } catch { /* ignore */ }
  }

  const activeSkills = skills.filter(s => s.enabled).length
  const builtinSkills = skills.filter(s => s.readonly).length
  const customSkills = skills.filter(s => !s.readonly).length

  return (
    <div className="flex flex-col h-full">
      {/* Header with sub-tabs */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px 28px', borderBottom: '1px solid var(--line)',
      }}>
        <div>
          <h2 style={{ fontFamily: 'var(--display)', fontSize: 18, letterSpacing: '.1em', textTransform: 'uppercase' }}>
            Skills & Tools
          </h2>
          <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginTop: 2 }}>
            {activeView === 'skills'
              ? `${builtinSkills} built-in · ${customSkills} custom · ${activeSkills} active`
              : `${tools.length} solidified tool${tools.length !== 1 ? 's' : ''}`
            }
          </p>
        </div>

        {/* Sub-tab toggle */}
        <div style={{ display: 'flex', gap: 0 }}>
          <button
            onClick={() => setActiveView('skills')}
            style={{
              fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 600,
              letterSpacing: '.06em', textTransform: 'uppercase',
              padding: '8px 18px', border: '1px solid var(--line)',
              borderRadius: 'var(--radius-sm) 0 0 var(--radius-sm)', cursor: 'pointer',
              background: activeView === 'skills' ? 'var(--accent)' : 'var(--bg)',
              color: activeView === 'skills' ? '#fff' : 'var(--mid)',
              transition: 'all .15s',
            }}
          >
            Skills
          </button>
          <button
            onClick={() => setActiveView('tools')}
            style={{
              fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 600,
              letterSpacing: '.06em', textTransform: 'uppercase',
              padding: '8px 18px', border: '1px solid var(--line)', borderLeft: 'none',
              borderRadius: '0 var(--radius-sm) var(--radius-sm) 0', cursor: 'pointer',
              background: activeView === 'tools' ? 'var(--accent)' : 'var(--bg)',
              color: activeView === 'tools' ? '#fff' : 'var(--mid)',
              transition: 'all .15s',
            }}
          >
            Tools
          </button>
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-y-auto">
        {activeView === 'skills' ? (
          <SkillsView
            skills={skills}
            loading={loadingSkills}
            onToggle={handleToggle}
            onView={handleViewSkill}
            onAdd={() => setShowAdd(true)}
            onImport={() => setShowImport(true)}
            onRefresh={fetchSkills}
          />
        ) : (
          <ToolsView
            tools={tools}
            loading={loadingTools}
            onView={handleViewTool}
            onRefresh={fetchTools}
            onDelete={handleDeleteTool}
          />
        )}
      </div>

      {/* Modals */}
      {viewSkill && (
        <SkillViewModal skill={viewSkill} onClose={() => setViewSkill(null)} onDelete={handleDeleteSkill} />
      )}
      {viewTool && (
        <ToolViewModal tool={viewTool} onClose={() => setViewTool(null)} onDelete={handleDeleteTool} />
      )}
      {showAdd && (
        <SkillAddModal onClose={() => setShowAdd(false)} onSaved={() => { setShowAdd(false); fetchSkills() }} />
      )}
      {showImport && (
        <SkillImportModal onClose={() => setShowImport(false)} onImported={() => { setShowImport(false); fetchSkills() }} />
      )}
    </div>
  )
}

/* ── Skills View ── */
function SkillsView({ skills, loading, onToggle, onView, onAdd, onImport, onRefresh }: {
  skills: Skill[]
  loading: boolean
  onToggle: (id: string, enabled: boolean) => void
  onView: (id: string) => void
  onAdd: () => void
  onImport: () => void
  onRefresh: () => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const text = await file.text()

    // Parse YAML frontmatter
    const fmMatch = text.match(/^---\s*\n([\s\S]*?)\n---\s*\n/)
    const meta: Record<string, string> = {}
    let content = text
    if (fmMatch) {
      try {
        const lines = fmMatch[1].split('\n')
        for (const line of lines) {
          const m = line.match(/^(\w+)\s*:\s*(.+)/)
          if (m) meta[m[1]] = m[2].replace(/^["']|["']$/g, '')
        }
      } catch { /* ignore */ }
      content = text.slice(fmMatch[0].length)
    }

    try {
      const resp = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: meta.name || file.name.replace(/\.md$/, ''),
          description: meta.description || '',
          version: meta.version || '1.0',
          author: meta.author || '',
          module: meta.module || 'global',
          enabled: true,
          content,
        }),
      })
      if (!resp.ok) alert(`Failed to upload skill (${resp.status})`)
      else onRefresh()
    } catch { /* ignore */ }

    if (fileRef.current) fileRef.current.value = ''
  }

  return (
    <div className="p-6">
      {/* Action bar */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
        <button className="btn-primary" onClick={onAdd} style={{ fontSize: 11, padding: '8px 14px' }}>
          + Add Skill
        </button>
        <button className="btn-secondary" onClick={onImport} style={{ fontSize: 11, padding: '8px 14px' }}>
          Import from GitHub
        </button>
        <button className="btn-secondary" onClick={() => fileRef.current?.click()} style={{ fontSize: 11, padding: '8px 14px' }}>
          Upload .md File
        </button>
        <input ref={fileRef} type="file" accept=".md" style={{ display: 'none' }} onChange={handleFileUpload} />
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--faint)', fontFamily: 'var(--mono)', fontSize: 12 }}>Loading...</div>
      ) : skills.length === 0 ? (
        <EmptyState onAdd={onAdd} onImport={onImport} type="skills" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
          {skills.map(s => (
            <SkillCard key={s.id} skill={s} onToggle={onToggle} onView={onView} />
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Tools View ── */
function ToolsView({ tools, loading, onView, onRefresh, onDelete }: {
  tools: ToolInfo[]
  loading: boolean
  onView: (name: string) => void
  onRefresh: () => void
  onDelete: (name: string) => void
}) {
  return (
    <div className="p-6">
      {/* Action bar */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, alignItems: 'center' }}>
        <button className="btn-secondary" onClick={onRefresh} style={{ fontSize: 11, padding: '8px 14px' }}>
          Refresh
        </button>
        <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)' }}>
          Solidified tools from MCP Bridge code generation pipeline
        </span>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--faint)', fontFamily: 'var(--mono)', fontSize: 12 }}>Loading...</div>
      ) : tools.length === 0 ? (
        <EmptyState onAdd={() => {}} onImport={() => {}} type="tools" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
          {tools.map(t => (
            <ToolCard key={t.name} tool={t} onView={onView} onDelete={onDelete} />
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Empty state ── */
function EmptyState({ onAdd, onImport, type }: { onAdd: () => void; onImport: () => void; type: 'skills' | 'tools' }) {
  return (
    <div style={{ maxWidth: 480, margin: '60px auto', textAlign: 'center' }}>
      <h3 style={{ fontFamily: 'var(--display)', fontSize: 16, letterSpacing: '.08em', textTransform: 'uppercase', marginBottom: 8 }}>
        {type === 'skills' ? 'No Skills Installed' : 'No Solidified Tools'}
      </h3>
      <p style={{ fontFamily: 'var(--serif)', fontSize: 15, color: 'var(--mid)', lineHeight: 1.6, marginBottom: 28 }}>
        {type === 'skills'
          ? 'Skills are AI behavioral protocols that enhance how modules respond. Create your own or import from GitHub.'
          : 'Tools are created by solidifying successful code executions in the MCP Bridge pipeline. Generate and execute code first, then solidify it as a reusable tool.'
        }
      </p>
      {type === 'skills' && (
        <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
          <button className="btn-primary" onClick={onAdd} style={{ fontSize: 11, padding: '10px 20px' }}>+ Create Skill</button>
          <button className="btn-secondary" onClick={onImport} style={{ fontSize: 11, padding: '10px 20px' }}>Import from GitHub</button>
        </div>
      )}
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
        borderRadius: 'var(--radius-sm)',
        cursor: 'pointer',
        transition: 'all .15s',
        opacity: skill.enabled ? 1 : 0.6,
        position: 'relative',
      }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = skill.enabled ? 'var(--subtle)' : 'var(--line)' }}
    >
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

      {isBuiltin && (
        <span style={{
          position: 'absolute', top: 14, right: 14,
          fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--faint)',
          padding: '2px 6px', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
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
        <span style={{
          fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 8px', borderRadius: 'var(--radius-sm)',
          background: srcColor + '12', color: srcColor, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '.04em',
        }}>
          {SOURCE_LABELS[skill.source || 'custom'] || skill.source}
        </span>
        {skill.layer && (
          <span style={{
            fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 7px', borderRadius: 'var(--radius-sm)',
            background: 'var(--line)', color: 'var(--mid)', textTransform: 'uppercase', fontWeight: 500, letterSpacing: '.03em',
          }}>
            {LAYER_LABELS[skill.layer] || skill.layer}
          </span>
        )}
        <span style={{
          fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 8px', borderRadius: 'var(--radius-sm)',
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

/* ── Tool card ── */
function ToolCard({ tool, onView, onDelete }: {
  tool: ToolInfo
  onView: (name: string) => void
  onDelete: (name: string) => void
}) {
  return (
    <div
      onClick={() => onView(tool.name)}
      style={{
        padding: '16px 18px',
        background: 'var(--bg)',
        border: '1px solid var(--subtle)',
        borderRadius: 'var(--radius-sm)',
        cursor: 'pointer',
        transition: 'all .15s',
        position: 'relative',
      }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--subtle)' }}
    >
      {/* Delete button */}
      <button
        onClick={e => { e.stopPropagation(); onDelete(tool.name) }}
        style={{
          position: 'absolute', top: 12, right: 14,
          fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 8px',
          border: '1px solid rgba(179, 59, 46, 0.32)', borderRadius: 'var(--radius-sm)',
          background: 'transparent', color: 'var(--danger)', cursor: 'pointer',
          transition: 'all .15s',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(179, 59, 46, 0.1)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
      >
        DELETE
      </button>

      <div style={{ fontFamily: 'var(--display)', fontSize: 14, fontWeight: 500, letterSpacing: '.05em', marginBottom: 6, paddingRight: 70 }}>
        {tool.display_name || tool.name}
      </div>

      <p style={{ fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--mid)', lineHeight: 1.5, marginBottom: 12, minHeight: 40 }}>
        {tool.description || 'No description'}
      </p>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{
          fontFamily: 'var(--mono)', fontSize: 9, padding: '2px 8px', borderRadius: 'var(--radius-sm)',
          background: 'rgba(154, 90, 22, 0.12)', color: 'var(--warning)', textTransform: 'uppercase', fontWeight: 600, letterSpacing: '.04em',
        }}>
          SOLIDIFIED
        </span>
        <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)' }}>
          {tool.execution_count} use{tool.execution_count !== 1 ? 's' : ''}
        </span>
        {tool.parameters?.length > 0 && (
          <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)' }}>
            {tool.parameters.length} param{tool.parameters.length !== 1 ? 's' : ''}
          </span>
        )}
        {tool.tags?.length > 0 && (
          <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)' }}>
            {tool.tags.join(', ')}
          </span>
        )}
      </div>
    </div>
  )
}

/* ── Skill view modal ── */
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
                fontFamily: 'var(--mono)', fontSize: 10, padding: '4px 10px', border: '1px solid rgba(179, 59, 46, 0.34)',
                background: 'transparent', color: 'var(--danger)', cursor: 'pointer', transition: 'all .15s',
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
          background: 'var(--bg2)', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
          padding: '16px 20px', maxHeight: 500, overflow: 'auto',
          fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.7, color: 'var(--dark)',
        }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.content || '(empty)'}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

/* ── Tool view modal ── */
function ToolViewModal({ tool, onClose, onDelete }: {
  tool: ToolInfo & { code_template?: string; source_query?: string }
  onClose: () => void
  onDelete: (name: string) => void
}) {
  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={{ ...modalStyle, maxWidth: 760 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
          <div>
            <h3 style={{ fontFamily: 'var(--display)', fontSize: 16, letterSpacing: '.08em', textTransform: 'uppercase' }}>
              {tool.display_name || tool.name}
            </h3>
            <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginTop: 4 }}>
              Solidified Tool &middot; {tool.execution_count} uses
              {tool.parameters?.length ? ` · ${tool.parameters.length} params` : ''}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={() => onDelete(tool.name)} style={{
              fontFamily: 'var(--mono)', fontSize: 10, padding: '4px 10px', border: '1px solid rgba(179, 59, 46, 0.34)',
              background: 'transparent', color: 'var(--danger)', cursor: 'pointer', transition: 'all .15s',
            }}>Delete</button>
            <button onClick={onClose} style={{
              fontFamily: 'var(--mono)', fontSize: 10, padding: '4px 10px', border: '1px solid var(--subtle)',
              background: 'transparent', color: 'var(--mid)', cursor: 'pointer',
            }}>Close</button>
          </div>
        </div>

        {tool.description && (
          <p style={{ fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--mid)', marginBottom: 16, lineHeight: 1.5 }}>
            {tool.description}
          </p>
        )}

        {tool.source_query && (
          <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', marginBottom: 12 }}>
            Source query: {tool.source_query}
          </p>
        )}

        {/* Parameters */}
        {tool.parameters?.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <h4 style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 8 }}>
              Parameters
            </h4>
            <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--mono)', fontSize: 12 }}>
                <thead>
                  <tr style={{ background: 'var(--bg2)' }}>
                    <th style={{ padding: '6px 12px', textAlign: 'left', fontWeight: 500, color: 'var(--mid)' }}>Name</th>
                    <th style={{ padding: '6px 12px', textAlign: 'left', fontWeight: 500, color: 'var(--mid)' }}>Type</th>
                    <th style={{ padding: '6px 12px', textAlign: 'left', fontWeight: 500, color: 'var(--mid)' }}>Description</th>
                    <th style={{ padding: '6px 12px', textAlign: 'left', fontWeight: 500, color: 'var(--mid)' }}>Default</th>
                  </tr>
                </thead>
                <tbody>
                  {tool.parameters.map(p => (
                    <tr key={p.name} style={{ borderTop: '1px solid var(--line)' }}>
                      <td style={{ padding: '6px 12px', fontWeight: 500 }}>{p.name}</td>
                      <td style={{ padding: '6px 12px', color: 'var(--mid)' }}>{p.type || 'string'}</td>
                      <td style={{ padding: '6px 12px', fontFamily: 'var(--serif)', color: 'var(--mid)' }}>{p.description || '-'}</td>
                      <td style={{ padding: '6px 12px', color: 'var(--faint)' }}>{p.default != null ? String(p.default) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Code template */}
        {tool.code_template && (
          <div>
            <h4 style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 8 }}>
              Code Template
            </h4>
            <pre style={{
              background: 'var(--bg2)', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
              padding: '16px 20px', maxHeight: 400, overflow: 'auto',
              fontFamily: 'var(--mono)', fontSize: 12, lineHeight: 1.6, color: 'var(--dark)',
              whiteSpace: 'pre-wrap',
            }}>
              {tool.code_template}
            </pre>
          </div>
        )}
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
    } catch (e: unknown) {
      setError(getErrorMessage(e))
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
              <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--danger)', marginBottom: 12, padding: '8px 12px', background: 'rgba(179, 59, 46, 0.1)', border: '1px solid rgba(179, 59, 46, 0.22)', borderRadius: 'var(--radius-sm)' }}>
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
              padding: 16, background: 'var(--bg2)', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)', marginBottom: 16,
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
  background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
  padding: '24px 28px', width: '100%', maxHeight: '85vh', overflow: 'auto',
  boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
}

const labelStyle: React.CSSProperties = {
  fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--faint)',
  textTransform: 'uppercase', letterSpacing: '.05em', display: 'block', marginBottom: 4,
}
