/* Collapsible thinking display with streaming markdown */

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  content: string
}

export default function ThinkingPanel({ content }: Props) {
  if (!content) return null

  return (
    <div className="thinking-scroll">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}
