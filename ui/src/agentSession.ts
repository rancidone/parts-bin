import type { AgentEvent, ConversationSummary } from './types.ts'

const THREAD_KEY = 'parts-bin.current-thread'
const kinds = new Set(['user_message', 'assistant_text', 'tool_call', 'tool_result', 'approval_request', 'approval_decision', 'error', 'completed'])

export async function readEventStream(response: Response, receive: (event: AgentEvent) => void) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('The server did not return an event stream.')
  const decoder = new TextDecoder()
  let buffer = ''
  function processChunk(chunk: string) {
    const lines = chunk.split('\n').map(line => line.replace(/\r$/, ''))
    if (lines.find(line => line.startsWith('event:'))?.slice(6).trim() !== 'agent_event') return
    const raw = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
    const event = JSON.parse(raw) as AgentEvent
    if (!kinds.has(event.kind) || typeof event.thread_id !== 'string' || typeof event.runtime !== 'string'
      || !Number.isInteger(event.sequence) || event.sequence < 1 || !event.data || typeof event.data !== 'object' || Array.isArray(event.data)) {
      throw new Error('The server returned an invalid chat event. Refresh history before continuing.')
    }
    receive(event)
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const chunks = buffer.split(/\r?\n\r?\n/)
      buffer = chunks.pop() ?? ''
      chunks.forEach(processChunk)
      if (done) {
        if (buffer.trim()) processChunk(buffer)
        return
      }
    }
  } finally { reader.releaseLock() }
}

export function approvalDecision(events: AgentEvent[], requestId: string): boolean | undefined {
  const decision = events.find(event => event.kind === 'approval_decision' && event.data.request_id === requestId)
  return decision ? decision.data.approved === true : undefined
}

export function unfinishedExecutions(events: AgentEvent[]) {
  const executions = new Map<string, { id: string; status: string; photo: boolean; needsPhoto: boolean }>()
  for (const event of events) {
    const id = event.data.execution_id
    if (typeof id !== 'string') continue
    const execution = executions.get(id) ?? { id, status: 'unfinished', photo: false, needsPhoto: false }
    if (event.kind === 'user_message') execution.photo = Boolean(event.data.image)
    if (event.kind === 'assistant_text' || event.kind === 'tool_call') execution.needsPhoto = false
    else if (event.kind === 'user_message') execution.needsPhoto = execution.photo
    if (event.kind === 'completed') execution.status = String(event.data.status)
    else if (event.kind !== 'error') execution.status = 'unfinished'
    executions.set(id, execution)
  }
  return [...executions.values()].filter(execution => !['completed', 'failed'].includes(execution.status))
}

export interface AgentSnapshot {
  events: AgentEvent[]
  conversations: ConversationSummary[]
  threadId: string | null
  pending: boolean
  error: string | null
  restored: boolean
}

export class AgentSession {
  private state: AgentSnapshot
  private listeners = new Set<() => void>()
  private fetcher: typeof fetch
  private storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
  private localSequence = 0
  private initialized = false

  constructor(fetcher: typeof fetch, storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
    this.fetcher = fetcher
    this.storage = storage
    let threadId = null
    try { threadId = storage?.getItem(THREAD_KEY) || null } catch { /* Browser storage may be disabled. */ }
    this.state = { events: [], conversations: [], threadId, pending: false, error: null, restored: !threadId }
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<AgentSnapshot>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }
  private receive(event: AgentEvent, echo?: number) {
    if (event.thread_id !== this.state.threadId) throw new Error('Received an event for another conversation.')
    const events = this.state.events.filter(item => !(echo !== undefined && event.kind === 'user_message' && item.sequence === echo))
    if (!events.some(item => item.sequence === event.sequence && item.thread_id === event.thread_id)) events.push(event)
    const ordered = [...events.filter(item => item.sequence > 0).sort((a, b) => a.sequence - b.sequence),
      ...events.filter(item => item.sequence < 0)]
    this.update({ events: ordered, conversations: this.withActiveSummary(ordered, event.runtime) })
  }
  private async request(url: string, init?: RequestInit) {
    const response = await this.fetcher(url, init)
    if (!response.ok) {
      let message = `Chat request failed (${response.status}).`
      try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail } catch { /* Keep the status message. */ }
      throw new Error(message)
    }
    return response
  }
  private async exclusive(action: () => Promise<void>): Promise<boolean> {
    if (this.state.pending) return false
    this.update({ pending: true, error: null })
    try { await action(); return true }
    catch (error) { this.update({ restored: false, error: error instanceof Error ? error.message : 'Could not reach the server.' }); return false }
    finally { this.update({ pending: false }) }
  }
  private assertWritable() {
    const selected = this.state.conversations.find(item => item.thread_id === this.state.threadId)
    if (selected?.runtime !== undefined && selected.runtime !== 'openai'
      || this.state.events.some(event => event.runtime !== 'openai')) {
      throw new Error('This conversation uses a retired provider. Start a new chat to continue.')
    }
  }
  private async consume(response: Response, echo?: number) {
    let completed = false
    await readEventStream(response, event => { this.receive(event, echo); if (event.kind === 'completed') completed = true })
    if (!completed) throw new Error('The response was interrupted. Refresh history before resuming; do not resend the message.')
  }
  private async loadConversations() {
    const response = await this.request('/agent/threads')
    const body = await response.json() as { threads?: unknown }
    if (!Array.isArray(body.threads)) throw new Error('The server did not return a conversation list.')
    const conversations = body.threads.map(item => {
      if (!item || typeof item !== 'object') throw new Error('The server returned an invalid conversation list.')
      const thread = item as Partial<ConversationSummary>
      if (typeof thread.thread_id !== 'string' || !thread.thread_id || typeof thread.runtime !== 'string'
        || typeof thread.title !== 'string' || !Number.isInteger(thread.last_sequence) || thread.last_sequence! < 0) {
        throw new Error('The server returned an invalid conversation list.')
      }
      return thread as ConversationSummary
    })
    this.update({ conversations })
  }
  private async loadEvents(threadId: string) {
    const response = await this.request(`/agent/threads/${encodeURIComponent(threadId)}/events`)
    const events: AgentEvent[] = []
    await readEventStream(response, event => {
      if (event.thread_id !== threadId) throw new Error('Received an event for another conversation.')
      if (!events.some(item => item.sequence === event.sequence)) events.push(event)
    })
    return events.sort((a, b) => a.sequence - b.sequence)
  }
  private async replay() {
    if (!this.state.threadId) { this.update({ restored: true, events: [] }); return }
    // Read-only recovery, never resubmit a message or paid stage automatically.
    const events = await this.loadEvents(this.state.threadId)
    this.update({ restored: true, events })
  }
  private withActiveSummary(events: AgentEvent[], runtime: string) {
    const threadId = this.state.threadId
    if (!threadId) return this.state.conversations
    const existing = this.state.conversations.find(item => item.thread_id === threadId)
    const text = events.find(item => item.kind === 'user_message' && String(item.data.text ?? '').trim())
      ?? events.find(item => item.kind === 'assistant_text' && String(item.data.text ?? '').trim())
    const photo = events.some(item => item.kind === 'user_message' && item.data.image)
    const rawTitle = text ? String(text.data.text).trim().replace(/\s+/g, ' ') : photo ? 'Photo request' : 'Empty conversation'
    const title = rawTitle.length > 62 ? `${rawTitle.slice(0, 61)}…` : rawTitle
    const summary: ConversationSummary = { thread_id: threadId, runtime: existing?.runtime ?? runtime, title,
      last_sequence: Math.max(0, ...events.map(item => item.sequence)) }
    return [summary, ...this.state.conversations.filter(item => item.thread_id !== threadId)]
  }
  restore = async () => {
    if (this.initialized) return
    this.initialized = true
    await this.refresh()
  }
  refresh = () => this.exclusive(async () => { await this.loadConversations(); await this.replay() })
  selectConversation = async (threadId: string): Promise<boolean> => {
    if (this.state.pending || threadId === this.state.threadId) return false
    if (!this.state.conversations.some(item => item.thread_id === threadId)) {
      this.update({ error: 'That conversation is no longer available.' })
      return false
    }
    this.update({ pending: true, error: null })
    try {
      const events = await this.loadEvents(threadId)
      try { this.storage?.setItem(THREAD_KEY, threadId) } catch { /* Session still works in memory. */ }
      this.update({ threadId, events, restored: true })
      return true
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : 'Could not reach the server.' })
      return false
    } finally { this.update({ pending: false }) }
  }
  newChat = () => {
    if (this.state.pending) return
    try { this.storage?.removeItem(THREAD_KEY) } catch { /* Session still works in memory. */ }
    this.update({ threadId: null, events: [], error: null, restored: true })
  }
  submit = (message: string, photo?: File) => this.exclusive(async () => {
    if (!this.state.restored) throw new Error('Refresh history before sending a message.')
    this.assertWritable()
    if (!message.trim() && !photo) return
    if (!this.state.threadId) {
      const response = await this.request('/agent/threads', { method: 'POST' })
      const created = await response.json() as { thread_id: string }
      if (typeof created.thread_id !== 'string' || !created.thread_id) throw new Error('The server did not return a conversation ID.')
      this.update({ threadId: created.thread_id })
      this.update({ conversations: [{ thread_id: created.thread_id, runtime: 'openai', title: 'Empty conversation', last_sequence: 0 },
        ...this.state.conversations.filter(item => item.thread_id !== created.thread_id)] })
      try { this.storage?.setItem(THREAD_KEY, created.thread_id) } catch { /* Session still works in memory. */ }
    }
    const echo = --this.localSequence
    this.update({ events: [...this.state.events, { kind: 'user_message', thread_id: this.state.threadId!, runtime: 'openai', sequence: echo,
      data: { text: message, image: photo ? { media_type: photo.type } : null } }] })
    const form = new FormData()
    form.append('message', message)
    if (photo) form.append('photo', photo)
    const response = await this.request(`/agent/threads/${encodeURIComponent(this.state.threadId!)}/messages`, { method: 'POST', body: form })
    await this.consume(response, echo)
    if (this.state.events.some(event => event.sequence === echo)) {
      throw new Error('The message was not acknowledged. Refresh history before sending it again.')
    }
  })
  decide = (requestId: string, approved: boolean) => this.exclusive(async () => {
    if (!this.state.restored || !this.state.threadId) throw new Error('Refresh history before deciding.')
    this.assertWritable()
    if (approvalDecision(this.state.events, requestId) !== undefined) return
    const response = await this.request(`/agent/threads/${encodeURIComponent(this.state.threadId!)}/approvals`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: requestId, approved }),
    })
    await this.consume(response)
  })
  resume = (executionId: string, photo?: File) => this.exclusive(async () => {
    await this.replay()
    this.assertWritable()
    const execution = unfinishedExecutions(this.state.events).find(item => item.id === executionId)
    if (!execution) return
    if (execution.status === 'awaiting_approval') throw new Error('Approve or decline the pending proposal to continue.')
    if (execution.needsPhoto && !photo) throw new Error('Attach a fresh photo before resuming this image request.')
    const form = new FormData()
    form.append('execution_id', executionId)
    if (photo) form.append('photo', photo)
    const response = await this.request(`/agent/threads/${encodeURIComponent(this.state.threadId!)}/resume`, { method: 'POST', body: form })
    await this.consume(response)
  })
}
