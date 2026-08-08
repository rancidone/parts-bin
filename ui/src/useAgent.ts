import { useState } from 'react'
import type { AgentEvent, RuntimeName } from './types'

async function readEventStream(response: Response, receive: (event: AgentEvent) => void) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('The server did not return an event stream.')
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) return
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const eventName = chunk.match(/^event: (.+)$/m)?.[1]
      const raw = chunk.match(/^data: (.+)$/m)?.[1]
      if (eventName !== 'agent_event' || !raw) continue
      try { receive(JSON.parse(raw) as AgentEvent) } catch { /* ignore malformed network data */ }
    }
  }
}

export function useAgent() {
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [threadId, setThreadId] = useState<string | null>(null)
  const [runtime, setRuntime] = useState<RuntimeName>('codex')
  const [pending, setPending] = useState(false)

  function receive(event: AgentEvent) {
    setEvents(previous => previous.some(item => item.sequence === event.sequence && item.thread_id === event.thread_id)
      ? previous : [...previous, event])
  }

  async function ensureThread() {
    if (threadId) return threadId
    const response = await fetch('/agent/threads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ runtime }),
    })
    if (!response.ok) throw new Error(await response.text())
    const created = await response.json() as { thread_id: string }
    setThreadId(created.thread_id)
    return created.thread_id
  }

  async function submit(message: string, photo?: File) {
    setPending(true)
    try {
      const id = await ensureThread()
      const form = new FormData()
      form.append('message', message)
      if (photo) form.append('photo', photo)
      const response = await fetch(`/agent/threads/${id}/messages`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(await response.text())
      await readEventStream(response, receive)
    } catch (error) {
      receive({ kind: 'error', thread_id: threadId ?? 'new', runtime, sequence: -Date.now(), data: { code: 'network_error', message: error instanceof Error ? error.message : 'Could not reach the server.' } })
    } finally { setPending(false) }
  }

  async function decide(requestId: string, approved: boolean) {
    if (!threadId) return
    setPending(true)
    try {
      const response = await fetch(`/agent/threads/${threadId}/approvals`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: requestId, approved }),
      })
      if (!response.ok) throw new Error(await response.text())
      await readEventStream(response, receive)
    } catch (error) {
      receive({ kind: 'error', thread_id: threadId, runtime, sequence: -Date.now(), data: { code: 'network_error', message: error instanceof Error ? error.message : 'Could not reach the server.' } })
    } finally { setPending(false) }
  }

  return { events, runtime, setRuntime, threadId, pending, submit, decide }
}
