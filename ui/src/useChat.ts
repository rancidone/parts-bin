import { useState } from 'react'
import type { Message, Part } from './types'

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([])

  function addMessage(msg: Message) {
    setMessages(prev => [...prev, msg])
  }

  async function send(text: string, photo?: File) {
    // Add user message immediately.
    const photoUrl = photo ? URL.createObjectURL(photo) : undefined
    addMessage({ role: 'user', text, photoUrl })

    const form = new FormData()
    form.append('message', text)
    if (photo) form.append('photo', photo)

    let response: Response
    try {
      response = await fetch('/chat', { method: 'POST', body: form })
    } catch {
      addMessage({ role: 'system', kind: 'error', text: 'Could not reach the server.' })
      return
    }

    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText)
      addMessage({ role: 'system', kind: 'error', text: detail })
      return
    }

    // Parse the SSE stream manually (EventSource doesn't support POST).
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

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
  }

  function handleEvent(event: string, data: Record<string, unknown>) {
    if (event === 'done') return

    if (event === 'error') {
      addMessage({ role: 'system', kind: 'error', text: String(data.message ?? 'Unknown error') })
      return
    }

    if (event === 'result') {
      const type = data.type as string

      if (type === 'ingest') {
        const part = data.part as Part
        addMessage({ role: 'system', kind: 'ingest-result', part })
        return
      }

      if (type === 'clarification') {
        addMessage({ role: 'system', kind: 'clarification', text: String(data.message ?? '') })
        return
      }

      if (type === 'query') {
        const matches = data.matches as Part[]
        if (matches.length === 0) {
          addMessage({
            role: 'system',
            kind: 'not-found',
            text: String(data.message ?? 'That part is not in your inventory.'),
          })
        } else {
          addMessage({ role: 'system', kind: 'query-result', matches })
        }
        return
      }
    }
  }

  return { messages, send }
}
