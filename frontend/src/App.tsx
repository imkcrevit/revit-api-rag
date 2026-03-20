/* Main App — Tab layout */

import { useState } from 'react'
import { useSettingsStore } from './store'
import { settingsApi } from './api/settings'
import Accordion from './components/shared/Accordion'
import CodeGenTab from './components/tabs/CodeGenTab'
import ApiExplorerTab from './components/tabs/ApiExplorerTab'
import BridgeTab from './components/tabs/BridgeTab'

const TABS = ['Code Generation', 'API Explorer', 'MCP Bridge'] as const

export default function App() {
  const [activeTab, setActiveTab] = useState(2) // Default to MCP Bridge
  const { apiKey, setApiKey, model, setModel, showFull, setShowFull, sessionId } = useSettingsStore()

  const handleSettingsChange = (key: string, m: string) => {
    settingsApi.update(key, m, sessionId).catch(() => {})
  }

  return (
    <div className="flex flex-col h-screen" style={{ background: 'var(--bg)' }}>
      {/* Header */}
      <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--line)' }}>
        <h1 className="heading-display text-xl" style={{ letterSpacing: '0.12em' }}>
          Revit API Assistant
        </h1>
        <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--faint)', letterSpacing: '0.05em', textTransform: 'uppercase', marginTop: 2 }}>
          Code Generation & Revit Execution
        </p>
      </div>

      {/* Settings */}
      <div className="px-6 pt-2">
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
      <div className="px-6 flex gap-0" style={{ borderBottom: '1px solid var(--line)' }}>
        {TABS.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(i)}
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 12,
              fontWeight: 500,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              padding: '10px 20px',
              border: 'none',
              borderBottom: activeTab === i ? '2px solid var(--accent)' : '2px solid transparent',
              background: 'transparent',
              color: activeTab === i ? 'var(--accent)' : 'var(--faint)',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 0 && <CodeGenTab />}
        {activeTab === 1 && <ApiExplorerTab />}
        {activeTab === 2 && <BridgeTab />}
      </div>
    </div>
  )
}
