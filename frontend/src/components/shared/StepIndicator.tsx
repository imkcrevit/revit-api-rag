/* Step progress bar — graptolite.ai style */

interface Props {
  steps: string[]
  current: number // 1-based
}

export default function StepIndicator({ steps, current }: Props) {
  return (
    <div className="flex items-center gap-1 py-1 flex-wrap" style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.04em' }}>
      {steps.map((label, i) => {
        const step = i + 1
        const isDone = step < current
        const isActive = step === current
        return (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <span style={{ color: 'var(--subtle)', margin: '0 4px' }}>&rarr;</span>}
            <span
              style={{
                color: isDone ? 'var(--mid)' : isActive ? 'var(--accent)' : 'var(--faint)',
                fontWeight: isActive ? 600 : 400,
                textDecorationLine: isActive ? 'underline' : 'none',
                textDecorationColor: 'var(--accent)',
                textUnderlineOffset: '4px',
              }}
            >
              {isDone ? '\u2713' : isActive ? '\u25B6' : ''} {step}.{label}
            </span>
          </span>
        )
      })}
    </div>
  )
}
