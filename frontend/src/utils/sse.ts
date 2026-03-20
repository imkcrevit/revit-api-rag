/* SSE thinking/code extraction — port of Python _parse_sse_stream */

export function extractThinkingAndCode(fullBuf: string): {
  thinking: string
  code: string
} {
  let thinking = ''
  let code = ''

  // Thinking: closed tag first, then open
  const closed = fullBuf.match(/<thinking>([\s\S]*?)<\/thinking>/)
  if (closed) {
    thinking = closed[1].trim()
  } else {
    const openM = fullBuf.match(/<thinking>([\s\S]*)/)
    if (openM) thinking = openM[1].trim()
  }

  // Code: after removing thinking block
  let after = fullBuf.replace(/<thinking>[\s\S]*?<\/thinking>/g, '')
  if (after.includes('<thinking>') && !after.includes('</thinking>')) {
    after = after.slice(0, after.indexOf('<thinking>'))
  }
  const cm = after.match(/```(?:csharp|cs)?\s*\n([\s\S]*?)(?:```|$)/)
  if (cm) code = cm[1].trim()

  return { thinking, code }
}
