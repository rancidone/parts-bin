import type { AgentEvent, ConversationSummary } from './types.ts'

class ProgressProtocolError extends Error {}
class InvalidFrame extends ProgressProtocolError {}
class ProgressDisconnected extends Error {}
class ChatRequestError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}
const retryable = (error: unknown) => !(error instanceof ProgressProtocolError)
  && (!(error instanceof ChatRequestError) || error.status >= 500 || error.status === 429)
const pause = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal.aborted) { reject(signal.reason); return }
  const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, milliseconds)
  function abort() { clearTimeout(timer); reject(signal.reason) }
  signal.addEventListener('abort', abort, { once: true })
})

const THREAD_KEY = 'parts-bin.current-thread'
const kinds = new Set(['user_message', 'assistant_text', 'tool_call', 'tool_result', 'approval_request', 'approval_decision', 'cancellation_requested', 'error', 'completed'])

export async function readEventStream(response: Response, receive: (event: AgentEvent) => void, idleTimeout = 15000) {
  const reader = response.body?.getReader()
  if (!reader) throw new ProgressProtocolError('The server did not return an event stream.')
  const decoder = new TextDecoder()
  let buffer = ''
  function processChunk(chunk: string) {
    const lines = chunk.split('\n').map(line => line.replace(/\r$/, ''))
    if (lines.find(line => line.startsWith('event:'))?.slice(6).trim() !== 'agent_event') return
    const raw = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
    let event: AgentEvent
    try { event = JSON.parse(raw) as AgentEvent }
    catch { throw new InvalidFrame('The server returned an invalid chat frame.') }
    if (!event || !kinds.has(event.kind) || typeof event.thread_id !== 'string' || typeof event.runtime !== 'string'
      || !Number.isSafeInteger(event.sequence) || event.sequence < 1 || !event.data || typeof event.data !== 'object' || Array.isArray(event.data)) {
      throw new ProgressProtocolError('The server returned an invalid chat event. Refresh history before continuing.')
    }
    receive(event)
  }
  try {
    while (true) {
      let timeout: ReturnType<typeof setTimeout> | undefined
      const disconnected = new Promise<never>((_, reject) => {
        timeout = setTimeout(() => reject(new ProgressDisconnected('The progress stream stopped responding.')), idleTimeout)
      })
      let chunk: ReadableStreamReadResult<Uint8Array>
      try { chunk = await Promise.race([reader.read(), disconnected]) }
      finally { clearTimeout(timeout) }
      const { done, value } = chunk
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const chunks = buffer.split(/\r?\n\r?\n/)
      buffer = chunks.pop() ?? ''
      chunks.forEach(processChunk)
      if (done) {
        if (buffer.trim()) {
          try { processChunk(buffer) }
          catch (error) { if (error instanceof InvalidFrame) throw new ProgressDisconnected('The last progress frame was truncated.'); throw error }
        }
        return
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}

export function approvalDecision(events: AgentEvent[], requestId: string): boolean | undefined {
  const decision = events.find(event => event.kind === 'approval_decision' && event.data.request_id === requestId)
  return decision ? decision.data.approved === true : undefined
}

export function unfinishedExecutions(events: AgentEvent[]) {
  const executions = new Map<string, { id: string; status: string; photo: boolean; needsPhoto: boolean; cancellationRequested: boolean }>()
  for (const event of events) {
    const id = event.data.execution_id
    if (typeof id !== 'string') continue
    const execution = executions.get(id) ?? { id, status: 'unfinished', photo: false, needsPhoto: false, cancellationRequested: false }
    if (event.kind === 'cancellation_requested') execution.cancellationRequested = true
    if (event.kind === 'user_message') execution.photo = Boolean(event.data.image)
    if (event.kind === 'assistant_text' || event.kind === 'tool_call') execution.needsPhoto = false
    else if (event.kind === 'user_message') execution.needsPhoto = execution.photo
    if (event.kind === 'completed' && event.data.status === 'busy') continue
    if (event.kind === 'completed') execution.status = String(event.data.status)
    else if (event.kind !== 'error') execution.status = 'unfinished'
    executions.set(id, execution)
  }
  return [...executions.values()].filter(execution => !['completed', 'failed', 'cancelled'].includes(execution.status))
}

export interface AgentSnapshot {
  events: AgentEvent[]
  conversations: ConversationSummary[]
  threadId: string | null
  pending: boolean
  cancelling: boolean
  watching: boolean
  reconnecting: boolean
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
  private active = false
  private watchEnabled = true
  private watchController?: AbortController
  private recovering?: AbortController
  private loading?: AbortController
  private restoring?: Promise<void>
  private lastError?: unknown

  constructor(fetcher: typeof fetch, storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
    this.fetcher = fetcher
    this.storage = storage
    let threadId = null
    try { threadId = storage?.getItem(THREAD_KEY) || null } catch { /* Browser storage may be disabled. */ }
    this.state = { events: [], conversations: [], threadId, pending: false, cancelling: false, watching: false, reconnecting: false, error: null, restored: !threadId }
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<AgentSnapshot>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }
  private receive(event: AgentEvent, echo?: number) {
    if (event.thread_id !== this.state.threadId) throw new ProgressProtocolError('Received an event for another conversation.')
    const events = this.state.events.filter(item => !(event.kind === 'user_message' && item.sequence < 0 && (item.sequence === echo || item.data.execution_id === event.data.execution_id)))
    if (!events.some(item => item.sequence === event.sequence && item.thread_id === event.thread_id)) events.push(event)
    const ordered = [...events.filter(item => item.sequence > 0).sort((a, b) => a.sequence - b.sequence),
      ...events.filter(item => item.sequence < 0)]
    this.update({ events: ordered, conversations: this.withActiveSummary(ordered, event.runtime) })
  }
  private async request(url: string, init?: RequestInit) {
    const options = init?.method === 'POST' ? init : { ...init,
      signal: init?.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000) }
    const response = await this.fetcher(url, options)
    if (!response.ok) {
      let message = `Chat request failed (${response.status}).`
      try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail } catch { /* Keep the status message. */ }
      throw new ChatRequestError(message, response.status)
    }
    return response
  }
  private async exclusive(action: () => Promise<void>): Promise<boolean> {
    if (this.state.pending) return false
    this.lastError = undefined
    this.update({ pending: true, error: null })
    try { await action(); return true }
    catch (error) { this.lastError = error; if (!(error instanceof DOMException && error.name === 'AbortError')) this.update({ restored: false, error: error instanceof Error ? error.message : 'Could not reach the server.' }); return false }
    finally { this.update({ pending: false }); this.startWatching() }
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
    if (!completed) throw new ProgressDisconnected('The progress stream disconnected.')
  }
  private async loadConversations(signal?: AbortSignal) {
    const response = await this.request('/agent/threads', signal ? { signal } : undefined)
    const body = await response.json() as { threads?: unknown }
    if (!Array.isArray(body.threads)) throw new ProgressProtocolError('The server did not return a conversation list.')
    const conversations = body.threads.map(item => {
      if (!item || typeof item !== 'object') throw new ProgressProtocolError('The server returned an invalid conversation list.')
      const thread = item as Partial<ConversationSummary>
      if (typeof thread.thread_id !== 'string' || !thread.thread_id || typeof thread.runtime !== 'string'
        || typeof thread.title !== 'string' || !Number.isInteger(thread.last_sequence) || thread.last_sequence! < 0) {
        throw new ProgressProtocolError('The server returned an invalid conversation list.')
      }
      return thread as ConversationSummary
    })
    signal?.throwIfAborted()
    this.update({ conversations })
  }
  private cursor() {
    // POST streams may omit events from another execution in this conversation.
    // Only advance over a contiguous prefix, so replay fills every gap.
    let cursor = 0
    for (const event of this.state.events) {
      if (event.sequence < 1 || event.sequence <= cursor) continue
      if (event.sequence !== cursor + 1) break
      cursor = event.sequence
    }
    return cursor
  }
  private async loadEvents(threadId: string, after?: number, executionId?: string, signal?: AbortSignal) {
    const query = new URLSearchParams()
    if (after !== undefined) query.set('after', String(after))
    if (executionId) query.set('execution_id', executionId)
    const response = await this.request(`/agent/threads/${encodeURIComponent(threadId)}/events${query.size ? `?${query}` : ''}`, signal ? { signal } : undefined)
    const active = response.headers.get('X-Agent-Active')
    const accepted = response.headers.get('X-Execution-Accepted')
    if (!['true', 'false'].includes(active ?? '') || executionId && !['true', 'false'].includes(accepted ?? '')) {
      throw new ProgressProtocolError('The server did not return valid progress state. Refresh the application.')
    }
    const events: AgentEvent[] = []
    await readEventStream(response, event => {
      if (event.thread_id !== threadId) throw new ProgressProtocolError('Received an event for another conversation.')
      if (!events.some(item => item.sequence === event.sequence)) events.push(event)
    })
    signal?.throwIfAborted()
    return { events: events.sort((a, b) => a.sequence - b.sequence), active: active === 'true', accepted: accepted === 'true' }
  }
  private async replay(signal?: AbortSignal) {
    if (!this.state.threadId) { this.active = false; this.update({ restored: true, events: [] }); return }
    const snapshot = await this.loadEvents(this.state.threadId, undefined, undefined, signal)
    this.active = snapshot.active
    this.update({ restored: true, events: snapshot.events })
  }
  private stopWatching() {
    this.watchController?.abort()
    this.watchController = undefined
    this.update({ watching: false, reconnecting: false })
  }
  disconnect = () => {
    this.watchEnabled = false
    this.stopWatching()
    this.recovering?.abort()
    this.loading?.abort()
  }
  private startWatching() {
    if (!this.watchEnabled || this.watchController || this.state.pending || !this.state.restored || !this.active || !this.state.threadId) return
    const threadId = this.state.threadId
    const controller = new AbortController()
    this.watchController = controller
    this.update({ watching: true })
    void (async () => {
      let failures = 0
      try {
        while (!controller.signal.aborted) {
          await pause(Math.min(10000, 1000 * 2 ** failures), controller.signal)
          try {
            const snapshot = await this.loadEvents(threadId, this.cursor(), undefined, controller.signal)
            if (this.watchController !== controller || this.state.threadId !== threadId) return
            snapshot.events.forEach(event => this.receive(event))
            this.active = snapshot.active
            failures = 0
            this.update({ reconnecting: false })
            if (!snapshot.active) return
          } catch (error) {
            if (controller.signal.aborted) return
            if (!retryable(error)) throw error
            failures = Math.min(failures + 1, 4)
            this.update({ reconnecting: true })
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) this.update({ restored: false, error: error instanceof Error ? error.message : 'Could not recover chat progress.' })
      } finally {
        if (this.watchController === controller) {
          this.watchController = undefined
          this.update({ watching: false, reconnecting: false })
        }
      }
    })()
  }
  private async sendProgress(url: string, init: RequestInit, executionId?: string, echo?: number) {
    this.stopWatching()
    try { await this.consume(await this.request(url, init), echo) }
    catch (error) {
      if (!retryable(error)) throw error
      const controller = new AbortController()
      this.recovering = controller
      this.update({ reconnecting: true })
      let failures = 0
      try {
        while (true) {
          try {
            const snapshot = await this.loadEvents(this.state.threadId!, this.cursor(), executionId, controller.signal)
            snapshot.events.forEach(event => this.receive(event))
            this.active = snapshot.active
            if (executionId && !snapshot.accepted) throw new ProgressProtocolError('The request was not acknowledged. Its draft has been retained; check history before sending it again.')
            if (echo !== undefined && this.state.events.some(event => event.sequence === echo) && snapshot.active) {
              await pause(1000, controller.signal)
              continue
            }
            return
          } catch (failure) {
            if (controller.signal.aborted || !retryable(failure)) throw failure
            await pause(Math.min(10000, 1000 * 2 ** failures), controller.signal)
            failures = Math.min(failures + 1, 4)
          }
        }
      } finally { this.recovering = undefined; this.update({ reconnecting: false }) }
    }
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
  restore = async (): Promise<void> => {
    this.watchEnabled = true
    if (this.initialized) { this.startWatching(); return }
    if (this.restoring) {
      await this.restoring
      if (this.watchEnabled && !this.initialized) await this.restore()
      return
    }
    const controller = new AbortController()
    this.loading = controller
    this.restoring = (async () => {
      let failures = 0
      try {
        while (!controller.signal.aborted) {
          const restored = await this.refresh(controller.signal)
          if (controller.signal.aborted) return
          if (restored || !retryable(this.lastError)) { this.initialized = true; return }
          this.update({ reconnecting: true })
          await pause(Math.min(10000, 1000 * 2 ** failures), controller.signal)
          failures = Math.min(failures + 1, 4)
        }
      } catch (error) {
        if (!controller.signal.aborted) this.update({ error: error instanceof Error ? error.message : 'Could not restore chat.' })
      } finally {
        if (this.loading === controller) { this.loading = undefined; this.update({ reconnecting: false }) }
      }
    })()
    try { await this.restoring }
    finally { this.restoring = undefined }
  }
  refresh = (signal?: AbortSignal) => this.exclusive(async () => {
    if (!signal) this.loading?.abort()
    this.stopWatching()
    await this.loadConversations(signal)
    await this.replay(signal)
  })
  selectConversation = async (threadId: string): Promise<boolean> => {
    if (this.state.pending || threadId === this.state.threadId) return false
    if (!this.state.conversations.some(item => item.thread_id === threadId)) {
      this.update({ error: 'That conversation is no longer available.' })
      return false
    }
    this.loading?.abort()
    this.stopWatching()
    this.update({ pending: true, error: null })
    try {
      const snapshot = await this.loadEvents(threadId)
      try { this.storage?.setItem(THREAD_KEY, threadId) } catch { /* Session still works in memory. */ }
      this.active = snapshot.active
      this.update({ threadId, events: snapshot.events, restored: true })
      return true
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : 'Could not reach the server.' })
      return false
    } finally { this.update({ pending: false }); this.startWatching() }
  }
  newChat = () => {
    if (this.state.pending) return
    this.loading?.abort()
    this.stopWatching()
    this.active = false
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
    const executionId = crypto.randomUUID()
    const echo = --this.localSequence
    this.update({ events: [...this.state.events, { kind: 'user_message', thread_id: this.state.threadId!, runtime: 'openai', sequence: echo,
      data: { execution_id: executionId, text: message, image: photo ? { media_type: photo.type } : null } }] })
    const form = new FormData()
    form.append('message', message)
    form.append('execution_id', executionId)
    if (photo) form.append('photo', photo)
    await this.sendProgress(`/agent/threads/${encodeURIComponent(this.state.threadId!)}/messages`, { method: 'POST', body: form }, executionId, echo)
    if (this.state.events.some(event => event.sequence === echo)) {
      throw new Error('The message was not acknowledged. Refresh history before sending it again.')
    }
  })
  cancel = async (executionId: string): Promise<boolean> => {
    if (this.state.cancelling || !this.state.threadId) return false
    const threadId = this.state.threadId
    this.update({ cancelling: true, error: null })
    let failure: unknown
    try {
      this.assertWritable()
      await this.request(`/agent/threads/${encodeURIComponent(threadId)}/executions/${encodeURIComponent(executionId)}/cancel`,
        { method: 'POST', signal: AbortSignal.timeout(15000) })
    } catch (error) { failure = error }
    try {
      // A lost acknowledgement is resolved through stored events; never replay intake.
      const snapshot = await this.loadEvents(threadId, this.cursor(), executionId)
      if (this.state.threadId !== threadId) return false
      snapshot.events.forEach(event => this.receive(event))
      this.active = snapshot.active
      const confirmed = this.state.events.some(event => event.data.execution_id === executionId
        && (event.kind === 'cancellation_requested' || event.kind === 'completed' && ['completed', 'failed', 'cancelled'].includes(String(event.data.status))))
      if (!confirmed) throw failure ?? new Error('Cancellation was not acknowledged. Refresh history or retry Cancel.')
      this.update({ restored: true })
      return true
    } catch (error) {
      if (this.state.threadId === threadId) this.update({ error: error instanceof Error ? error.message : 'Could not confirm cancellation. Refresh history or retry Cancel.' })
      return false
    } finally { this.update({ cancelling: false }); this.startWatching() }
  }
  decide = (requestId: string, approved: boolean) => this.exclusive(async () => {
    if (!this.state.restored || !this.state.threadId) throw new Error('Refresh history before deciding.')
    this.assertWritable()
    if (approvalDecision(this.state.events, requestId) !== undefined) return
    await this.sendProgress(`/agent/threads/${encodeURIComponent(this.state.threadId!)}/approvals`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: requestId, approved }),
    })
    if (approvalDecision(this.state.events, requestId) === undefined) {
      throw new ProgressProtocolError('The decision was not acknowledged. Check the saved proposal before deciding again.')
    }
  })
  resume = (executionId: string, photo?: File) => this.exclusive(async () => {
    await this.replay()
    this.assertWritable()
    const execution = unfinishedExecutions(this.state.events).find(item => item.id === executionId)
    if (!execution) return
    if (execution.cancellationRequested) throw new Error('Cancellation is irreversible. Retry Cancel to recover cleanup.')
    if (execution.status === 'awaiting_approval') throw new Error('Approve or decline the pending proposal to continue.')
    if (execution.needsPhoto && !photo) throw new Error('Attach a fresh photo before resuming this image request.')
    const form = new FormData()
    form.append('execution_id', executionId)
    if (photo) form.append('photo', photo)
    await this.sendProgress(`/agent/threads/${encodeURIComponent(this.state.threadId!)}/resume`, { method: 'POST', body: form }, executionId)
  })
}
