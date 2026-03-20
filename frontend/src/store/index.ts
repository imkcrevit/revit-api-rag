/* Zustand stores */

import { create } from 'zustand'

interface SettingsState {
  apiKey: string
  model: string
  showFull: boolean
  sessionId: string
  unit: string
  setApiKey: (k: string) => void
  setModel: (m: string) => void
  setShowFull: (v: boolean) => void
  setUnit: (u: string) => void
}

export const useSettingsStore = create<SettingsState>((set) => ({
  apiKey: '',
  model: 'anthropic/claude-sonnet-4.6',
  showFull: false,
  sessionId: crypto.randomUUID().replace(/-/g, ''),
  unit: 'mm',
  setApiKey: (apiKey) => set({ apiKey }),
  setModel: (model) => set({ model }),
  setShowFull: (showFull) => set({ showFull }),
  setUnit: (unit) => set({ unit }),
}))
