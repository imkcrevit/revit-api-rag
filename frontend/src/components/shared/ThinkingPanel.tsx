/* Collapsible thinking display with streaming markdown */

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  content: string
  title?: string
}

export default function ThinkingPanel({ content, title = 'Thinking Chain' }: Props) {
  if (!content) return null

  return (
    <div className="thinking-panel">
      <div className="thinking-title">{title}</div>
      <div className="thinking-scroll">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    </div>
  )
}
