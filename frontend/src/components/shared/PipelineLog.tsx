/* Scrollable pipeline stage log */

import { useEffect, useRef } from 'react'

interface Props {
  messages: string[]
}

export default function PipelineLog({ messages }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [messages])

  if (!messages.length) return null

  return (
    <div ref={ref} className="pipeline-log">
      {messages.map((msg, i) => (
        <div key={i} className={i === messages.length - 1 ? 'active' : 'done'}>
          {i === messages.length - 1 ? '\u25B6 ' : '\u2713 '}{msg}
        </div>
      ))}
    </div>
  )
}
