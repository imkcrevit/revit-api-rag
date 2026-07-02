/* Main App — Tab layout */

import { useState, useRef } from 'react'
import { useSettingsStore } from './store'
import { settingsApi } from './api/settings'
import Accordion from './components/shared/Accordion'
import CodeGenTab from './components/tabs/CodeGenTab'
import ApiExplorerTab from './components/tabs/ApiExplorerTab'
import BridgeTab from './components/tabs/BridgeTab'
import PromptBridgeTab from './components/tabs/PromptBridgeTab'
import TextStudioTab from './components/tabs/TextStudioTab'
import SkillsTab from './components/tabs/SkillsTab'

const TABS = ['Skills & Tools', 'Code Generation', 'API Explorer', 'MCP Bridge', 'PromptBridge', 'TextStudio'] as const

/* Experimental badge on specific tabs */
const EXPERIMENTAL_TABS = new Set(['TextStudio'])
const API_MODE = import.meta.env.VITE_API_BASE_URL || 'same-origin'

export default function App() {
  const [activeTab, setActiveTab] = useState(3) // Default to MCP Bridge
  const { apiKey, setApiKey, model, setModel, showFull, setShowFull, sessionId } = useSettingsStore()

  const settingsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /* Debounce settings sync so we don't POST on every keystroke of the API key. */
  const handleSettingsChange = (key: string, m: string) => {
    if (settingsDebounceRef.current) clearTimeout(settingsDebounceRef.current)
    settingsDebounceRef.current = setTimeout(() => {
      settingsApi.update(key, m, sessionId).catch(() => {})
    }, 500)
  }

  return (
    <div className="app-shell flex flex-col h-screen">
      {/* Header */}
      <div className="app-header">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">R</div>
          <div>
            <h1 className="app-title">
              Revit API Assistant
            </h1>
            <p className="app-kicker">
              BIM Retrieval · Code Generation · Revit Execution
            </p>
          </div>
        </div>
        <div className="runtime-chip">
          <span>API</span>
          <strong>{API_MODE}</strong>
        </div>
      </div>

      {/* Settings */}
      <div className="settings-strip">
        <Accordion title="Settings">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex-1 min-w-[200px]">
              <label className="label-text">API Key (optional)</label>
              <input
                type="password"
                className="input-field"
                placeholder="Leave empty to use system key"
                value={apiKey}
                onChange={e => { setApiKey(e.target.value); handleSettingsChange(e.target.value, model) }}
              />
            </div>
            <div className="min-w-[200px]">
              <label className="label-text">Model</label>
              <input
                className="input-field"
                value={model}
                onChange={e => { setModel(e.target.value); handleSettingsChange(apiKey, e.target.value) }}
              />
            </div>
            <label className="flex items-center gap-2" style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--mid)' }}>
              <input type="checkbox" checked={showFull} onChange={e => setShowFull(e.target.checked)} />
              Full Code
            </label>
          </div>
        </Accordion>
      </div>

      {/* Tab bar */}
      <div className="tab-bar">
        {TABS.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(i)}
            className={`tab-button${activeTab === i ? ' is-active' : ''}`}
          >
            {tab}
            {EXPERIMENTAL_TABS.has(tab) && (
              <span className="exp-badge">EXP</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 0 && <SkillsTab />}
        {activeTab === 1 && <CodeGenTab />}
        {activeTab === 2 && <ApiExplorerTab />}
        {activeTab === 3 && <BridgeTab />}
        {activeTab === 4 && <PromptBridgeTab />}
        {activeTab === 5 && <TextStudioTab />}
      </div>
    </div>
  )
}
