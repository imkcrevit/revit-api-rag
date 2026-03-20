/* Tab B: Text2Revit (Legacy) — SSE chat */

import ChatPanel from '../shared/ChatPanel'

export default function Text2RevitTab() {
  return (
    <ChatPanel
      endpoint="/api/t2r/chat"
      placeholder="Describe what to create in Revit... (wall, column, beam, floor, door, window)"
    />
  )
}
