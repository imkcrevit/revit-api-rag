/* Scrollable pipeline stage log */

import { useEffect, useRef } from 'react'

export interface PipelineTask {
  id: string
  label: string
  status: 'pending' | 'active' | 'done' | 'skipped' | 'error'
  detail?: string
}

interface Props {
  messages?: string[]
  tasks?: PipelineTask[]
}

export default function PipelineLog({ messages = [], tasks = [] }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [messages, tasks])

  if (!messages.length && !tasks.length) return null

  if (tasks.length) {
    return (
      <div ref={ref} className="pipeline-tasks">
        {tasks.map(task => (
          <div key={task.id} className={`pipeline-task ${task.status}`}>
            <div className="pipeline-task-marker" aria-hidden="true">
              {task.status === 'done' ? '\u2713' :
                task.status === 'active' ? '\u25B6' :
                  task.status === 'error' ? '!' :
                    task.status === 'skipped' ? '-' : ''}
            </div>
            <div className="pipeline-task-copy">
              <div className="pipeline-task-label">{task.label}</div>
              {task.detail && <div className="pipeline-task-detail">{task.detail}</div>}
            </div>
          </div>
        ))}
      </div>
    )
  }

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
