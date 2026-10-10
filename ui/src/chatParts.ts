import type { AgentEvent, Part } from './types'

// Resolve identities from this turn's returned records, never model prose.
export function partsByReply(events: AgentEvent[]): Map<number, Part[]> {
  const parts = new Map<number, Part>()
  const replies = new Map<number, Part[]>()
  for (const event of events) {
    if (event.kind === 'user_message') parts.clear()
    if (event.kind === 'assistant_text') replies.set(event.sequence, [...parts.values()])
    if (event.kind !== 'tool_result') continue
    const payload = event.data.result as { ok?: boolean; result?: Part | { parts?: Part[] } } | undefined
    if (!payload?.ok || !payload.result || typeof payload.result !== 'object') continue
    const value = payload.result
    const records = 'parts' in value && Array.isArray(value.parts) ? value.parts
      : 'part_category' in value ? [value as Part] : []
    for (const part of records) if (typeof part.id === 'number') parts.set(part.id, part)
  }
  return replies
}
