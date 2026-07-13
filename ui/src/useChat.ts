import { useRef, useState } from 'react'
import type { BatchSummary, Message, Part } from './types'

interface SendOptions {
  hideUserEcho?: boolean
  threadId?: string
}

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [pendingCount, setPendingCount] = useState(0)
  const mainThreadIdRef = useRef(globalThis.crypto?.randomUUID?.() ?? `main-${Date.now()}`)

  function addMessage(msg: Message) {
    setMessages(prev => [...prev, msg])
  }

  function chooseThreadId(text: string, photo?: File, explicitThreadId?: string) {
    if (explicitThreadId) return explicitThreadId
    if (photo || /^\s*add\b/i.test(text)) {
      return globalThis.crypto?.randomUUID?.() ?? `thread-${Date.now()}-${Math.random().toString(16).slice(2)}`
    }
    return mainThreadIdRef.current
  }

  async function send(text: string, photo?: File, options: SendOptions = {}) {
    // Add user message immediately.
    const photoUrl = photo ? URL.createObjectURL(photo) : undefined
    const threadId = chooseThreadId(text, photo, options.threadId)
    if (!options.hideUserEcho) {
      addMessage({ role: 'user', text, photoUrl })
    }

    const form = new FormData()
    form.append('message', text)
    if (photo) form.append('photo', photo)
    form.append('thread_id', threadId)

    let response: Response
    setPendingCount(count => count + 1)
    try {
      response = await fetch('/chat', { method: 'POST', body: form })
    } catch {
      addMessage({ role: 'system', kind: 'error', text: 'Could not reach the server.' })
      setPendingCount(count => Math.max(0, count - 1))
      return
    }

    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText)
      addMessage({ role: 'system', kind: 'error', text: detail })
      setPendingCount(count => Math.max(0, count - 1))
      return
    }

    // Parse the SSE stream manually (EventSource doesn't support POST).
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // Process complete SSE messages (separated by \n\n).
        const parts = buffer.split('\n\n')
        buffer = parts.pop()! // last chunk may be incomplete

        for (const chunk of parts) {
          const eventLine = chunk.match(/^event: (.+)$/m)?.[1]
          const dataLine = chunk.match(/^data: (.+)$/m)?.[1]
          if (!eventLine || !dataLine) continue

          let data: Record<string, unknown>
          try {
            data = JSON.parse(dataLine)
          } catch {
            continue
          }

          handleEvent(eventLine, data)
        }
      }
    } finally {
      setPendingCount(count => Math.max(0, count - 1))
    }
  }

  function handleEvent(event: string, data: Record<string, unknown>) {
    if (event === 'done') return

    if (event === 'error') {
      addMessage({ role: 'system', kind: 'error', text: String(data.message ?? 'Unknown error') })
      return
    }

    if (event === 'result') {
      const type = data.type as string

      if (type === 'chat') {
        addMessage({
          role: 'system',
          threadId: data.thread_id ? String(data.thread_id) : undefined,
          kind: 'chat',
          text: String(data.response ?? ''),
          part: (data.part as Part) ?? undefined,
          batchSummary: (data.batch_summary as BatchSummary) ?? undefined,
        })
        return
      }

      // Legacy types kept for backwards compatibility.
      if (type === 'ingest') {
        const part = data.part as Part
        addMessage({ role: 'system', kind: 'ingest-result', part })
        return
      }

      if (type === 'clarification') {
        addMessage({
          role: 'system',
          threadId: data.thread_id ? String(data.thread_id) : undefined,
          clarificationKind: data.clarification_kind === 'duplicate_upsert' ? 'duplicate_upsert' : undefined,
          kind: 'clarification',
          text: String(data.message ?? ''),
        })
        return
      }

      if (type === 'query') {
        const matches = data.matches as Part[]
        const response = data.response ? String(data.response) : undefined
        if (matches.length === 0) {
          addMessage({
            role: 'system',
            kind: 'not-found',
            text: response ?? 'That part is not in your inventory.',
          })
        } else {
          addMessage({
            role: 'system',
            threadId: data.thread_id ? String(data.thread_id) : undefined,
            kind: 'query-result',
            matches,
            text: response,
          })
        }
        return
      }
    }
  }

  return { messages, pendingCount, send }
}
