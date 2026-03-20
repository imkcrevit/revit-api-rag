/* Tab A: Code Generation — SSE chat */

import ChatPanel from '../shared/ChatPanel'

export default function CodeGenTab() {
  return <ChatPanel endpoint="/api/chat" placeholder="Ask about Revit API..." showFullOption />
}
