import { useEffect, useRef, useState } from 'react'
import { PartCard } from './PartCard'
import { useAgent } from './useAgent'
import type { AgentEvent, Part, RuntimeName } from './types'
import styles from './Chat.module.css'

const runtimeLabels: Record<RuntimeName, string> = { codex: 'Codex', openai: 'OpenAI API', local: 'Local' }

export function Chat() {
  const { events, runtime, setRuntime, threadId, pending, submit, decide } = useAgent()
  const [text, setText] = useState('')
  const [photo, setPhoto] = useState<File>()
  const [photoPreview, setPhotoPreview] = useState<string>()
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), [events])
  function clearPhoto() {
    setPhoto(undefined)
    if (photoPreview) URL.revokeObjectURL(photoPreview)
    setPhotoPreview(undefined)
    if (fileRef.current) fileRef.current.value = ''
  }
  function choosePhoto(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    clearPhoto(); setPhoto(file); setPhotoPreview(URL.createObjectURL(file))
  }
  function send(event: React.FormEvent) {
    event.preventDefault()
    if (!text.trim() && !photo) return
    void submit(text.trim(), photo); setText(''); clearPhoto()
  }

  return <div className={styles.container}>
    <div className={styles.thread}>
      {events.length === 0 && <div className={styles.empty}>Ask about your inventory, add a part, or attach a photo.</div>}
      {events.map(event => <EventBubble key={`${event.thread_id}-${event.sequence}`} event={event} decide={decide} />)}
      {pending && <div className={styles.thinkingBubble}><span className={styles.dot} /><span className={styles.dot} /><span className={styles.dot} /></div>}
      <div ref={bottomRef} />
    </div>
    <form className={styles.inputBar} onSubmit={send}>
      {photoPreview && <div className={styles.photoPreview}><img src={photoPreview} alt="attachment" /><button type="button" className={styles.removePhoto} onClick={clearPhoto}>✕</button></div>}
      <div className={styles.runtimeRow}>
        <label htmlFor="runtime">Runtime</label>
        <select id="runtime" value={runtime} disabled={Boolean(threadId)} onChange={event => setRuntime(event.target.value as RuntimeName)}>
          {Object.entries(runtimeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        {threadId && <span className={styles.runtimeLocked}>Selected for this conversation</span>}
      </div>
      <div className={styles.inputRow}>
        <button type="button" className={styles.attachBtn} onClick={() => fileRef.current?.click()} title="Attach photo">📎</button>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={choosePhoto} hidden />
        <textarea className={styles.textInput} value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(event as unknown as React.FormEvent) } }} placeholder="Add a part or ask a question…" rows={1} />
        <button type="submit" className={styles.sendBtn} disabled={pending || (!text.trim() && !photo)}>▶</button>
      </div>
    </form>
  </div>
}

function EventBubble({ event, decide }: { event: AgentEvent; decide: (requestId: string, approved: boolean) => Promise<void> }) {
  const data = event.data
  if (event.kind === 'user_message') return <div className={styles.userBubble}>{String(data.text ?? '')}{Boolean(data.image) && <span className={styles.attachment}>Photo attached</span>}</div>
  if (event.kind === 'assistant_text') return <div className={styles.assistantBubble}>{String(data.text ?? '')}</div>
  if (event.kind === 'tool_call') return <div className={styles.activity}>Using <strong>{String(data.name)}</strong></div>
  if (event.kind === 'tool_result') return <ToolResult result={data.result} />
  if (event.kind === 'approval_request') return <div className={styles.approval}><div><strong>Approval required</strong><br />{String(data.effect ?? data.tool)}</div><div className={styles.clarificationActions}><button className={styles.inlineActionBtn} onClick={() => void decide(String(data.request_id), true)}>Approve</button><button className={styles.inlineActionBtn} onClick={() => void decide(String(data.request_id), false)}>Decline</button></div></div>
  if (event.kind === 'approval_decision') return <div className={styles.activity}>You {data.approved ? 'approved' : 'declined'} {String(data.tool)}.</div>
  if (event.kind === 'error') return <div className={`${styles.assistantBubble} ${styles.errorBubble}`}>{String(data.message ?? 'Unknown error')}</div>
  return null
}

function ToolResult({ result }: { result: unknown }) {
  if (!result || typeof result !== 'object') return <div className={styles.activity}>Tool completed.</div>
  const payload = result as { ok?: boolean; result?: unknown; error?: { message?: string } }
  if (!payload.ok) return <div className={`${styles.activity} ${styles.error}`}>{payload.error?.message ?? 'Tool failed'}</div>
  const value = payload.result
  if (value && typeof value === 'object' && Array.isArray((value as { parts?: unknown[] }).parts)) {
    const parts = (value as { parts: Part[] }).parts
    return <div className={styles.systemMsg}><div className={styles.activity}>{parts.length} part{parts.length === 1 ? '' : 's'} found</div>{parts.map(part => <PartCard key={part.id} part={part} />)}</div>
  }
  if (value && typeof value === 'object' && 'part_category' in value) return <div className={styles.systemMsg}><PartCard part={value as Part} added /></div>
  return <div className={styles.activity}>Tool completed.</div>
}
