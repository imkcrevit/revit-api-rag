/* Simple collapsible accordion — graptolite.ai style */

import { useState, useRef, type ReactNode } from 'react'

interface Props {
  title: string
  defaultOpen?: boolean
  open?: boolean
  onToggle?: (open: boolean) => void
  children: ReactNode
  id?: string
}

export default function Accordion({ title, defaultOpen = false, open: controlledOpen, onToggle, children, id }: Props) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const isOpen = controlledOpen !== undefined ? controlledOpen : internalOpen
  const ref = useRef<HTMLDivElement>(null)

  const toggle = () => {
    const next = !isOpen
    setInternalOpen(next)
    onToggle?.(next)
  }

  return (
    <div ref={ref} id={id} className="card mb-2">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left"
        style={{
          fontFamily: 'var(--mono)',
          fontSize: 12,
          fontWeight: 500,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          color: 'var(--mid)',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
        }}
      >
        {title}
        <span style={{
          transition: 'transform 0.2s',
          transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)',
          color: 'var(--faint)',
          fontSize: 10,
        }}>&#9660;</span>
      </button>
      {isOpen && <div className="px-4 pb-3">{children}</div>}
    </div>
  )
}
