import { useEffect, useRef, useState } from 'react'
import { SpecificationReview, SpecificationResult } from './SpecificationFacts'
import { PartCard } from './PartCard'
import { PartResults } from './PartResults'
import { useAgent } from './useAgent'
import { approvalDecision, unfinishedExecutions } from './agentSession'
import type { AgentEvent, Part } from './types'
import styles from './Chat.module.css'

export function Chat() {
  const { events, pending, submit, decide, refresh, resume, newChat, error, restored } = useAgent()
  const unfinished = unfinishedExecutions(events)
  const readOnly = events.some(event => event.runtime !== 'openai')
  const [text, setText] = useState('')
  const [photo, setPhoto] = useState<File>()
  const [photoPreview, setPhotoPreview] = useState<string>()
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [events])
  useEffect(() => () => { if (photoPreview) URL.revokeObjectURL(photoPreview) }, [photoPreview])
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
    if (pending || !restored || readOnly || (!text.trim() && !photo)) return
    const message = text.trim()
    void submit(message, photo).then(accepted => { if (accepted) { setText(''); clearPhoto() } })
  }

  return <div className={styles.container}>
    <div className={styles.toolbar}>
      <span role="status">{!restored && !pending ? 'Refresh history to reconnect' : pending ? (restored ? 'Working…' : 'Restoring conversation…') : readOnly ? 'Read-only conversation' : unfinished.some(item => item.status === 'awaiting_approval') ? 'Awaiting approval' : unfinished.length ? 'Unfinished work' : events.length ? 'Ready' : 'New conversation'}</span>
      <button className={styles.inlineActionBtn} disabled={pending} onClick={() => void refresh()}>Refresh history</button>
      <button className={styles.inlineActionBtn} disabled={pending} onClick={() => { newChat(); setText(''); clearPhoto() }}>New chat</button>
    </div>
    {error && <div className={styles.notice} role="alert">{error}</div>}
    {readOnly && <div className={styles.notice}>This conversation uses a retired provider. Start a new chat to continue.</div>}
    <div className={styles.thread}>
      {events.length === 0 && <div className={styles.empty}>Ask about your inventory, add a part, or attach a photo.</div>}
      {events.map(event => <EventBubble key={`${event.thread_id}-${event.sequence}`} event={event} decide={decide} disabled={pending || !restored || readOnly || (typeof event.data.execution_id === 'string' && !unfinished.some(item => item.id === event.data.execution_id))} decision={approvalDecision(events, String(event.data.request_id))} />)}
      {!pending && !readOnly && unfinished.map(execution => <div key={execution.id} className={styles.recovery}>
        {execution.status === 'awaiting_approval' ? <span>Awaiting your approval above.</span> : <>
          <span>This request may still be running or was interrupted. Refresh history to check. Resuming may repeat a model call and incur another charge.</span>
          {execution.needsPhoto && <span>Attach a fresh photo below to resume image analysis.</span>}
          <button className={styles.inlineActionBtn} disabled={!restored || (execution.needsPhoto && !photo)} onClick={() => {
            void resume(execution.id, photo).then(accepted => { if (accepted) clearPhoto() })
          }}>Resume request</button>
        </>}
      </div>)}
      {pending && <div className={styles.thinkingBubble}><span className={styles.dot} /><span className={styles.dot} /><span className={styles.dot} /></div>}
      <div ref={bottomRef} />
    </div>
    <form className={styles.inputBar} onSubmit={send}>
      {photoPreview && <div className={styles.photoPreview}><img src={photoPreview} alt="attachment" /><button type="button" className={styles.removePhoto} onClick={clearPhoto}>✕</button></div>}
      <div className={styles.inputRow}>
        <button type="button" className={styles.attachBtn} disabled={pending || readOnly} onClick={() => fileRef.current?.click()} title="Attach photo">📎</button>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={choosePhoto} hidden />
        <textarea className={styles.textInput} disabled={pending || !restored || readOnly} aria-label="Message" value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(event as unknown as React.FormEvent) } }} placeholder="Add a part or ask a question…" rows={1} />
        <button type="submit" className={styles.sendBtn} aria-label="Send message" disabled={pending || !restored || readOnly || (!text.trim() && !photo)}>▶</button>
      </div>
    </form>
  </div>
}

function EventBubble({ event, decide, disabled, decision }: { event: AgentEvent; decide: (requestId: string, approved: boolean) => Promise<boolean>; disabled: boolean; decision?: boolean }) {
  const data = event.data
  if (event.kind === 'user_message') return <div className={styles.userBubble}>{String(data.text ?? '')}{Boolean(data.image) && <span className={styles.attachment}>Photo attached</span>}</div>
  if (event.kind === 'assistant_text') return <div className={styles.assistantBubble}>{String(data.text ?? '')}</div>
  if (event.kind === 'tool_call') return <div className={styles.activity}>Using <strong>{String(data.name)}</strong></div>
  if (event.kind === 'tool_result') return <ToolResult result={data.result} name={String(data.name)} />
  if (event.kind === 'approval_request') return <div className={styles.approval}><div><strong>{decision === undefined ? 'Approval required' : 'Review decision'}</strong><br />{String(data.effect ?? data.tool)}</div>{data.specification_review && typeof data.specification_review === 'object' && 'facts' in data.specification_review ? <SpecificationReview review={data.specification_review as Parameters<typeof SpecificationReview>[0]['review']} partId={String(data.target)} /> : null}{decision !== undefined ? <div className={styles.activity}>{decision ? 'Approved' : 'Declined'}</div> : <div className={styles.clarificationActions}><button className={styles.inlineActionBtn} disabled={disabled} onClick={() => void decide(String(data.request_id), true)}>Approve</button><button className={styles.inlineActionBtn} disabled={disabled} onClick={() => void decide(String(data.request_id), false)}>Decline</button></div>}</div>
  if (event.kind === 'approval_decision') return <div className={styles.activity}>You {data.approved ? 'approved' : 'declined'} {String(data.tool)}.</div>
  if (event.kind === 'error') return <div className={`${styles.assistantBubble} ${styles.errorBubble}`}>{String(data.message ?? 'Unknown error')}</div>
  return null
}

function ToolResult({ result, name }: { result: unknown; name: string }) {
  if (!result || typeof result !== 'object') return <div className={styles.activity}>Tool completed.</div>
  const payload = result as { ok?: boolean; result?: unknown; error?: { message?: string } }
  if (!payload.ok) return <div className={`${styles.activity} ${styles.error}`}>{payload.error?.message ?? 'Tool failed'}</div>
  const value = payload.result
  if (value && typeof value === 'object' && ('matches' in value || 'facts' in value)) return <div className={styles.systemMsg}><SpecificationResult value={value as Parameters<typeof SpecificationResult>[0]['value']} /></div>
  if (value && typeof value === 'object' && Array.isArray((value as { parts?: unknown[] }).parts)) {
    const { parts, count, truncated } = value as { parts: Part[]; count?: number; truncated?: boolean }
    return <div className={styles.systemMsg}><PartResults parts={parts} count={count} truncated={truncated} /></div>
  }
  if (value && typeof value === 'object' && 'part_category' in value) return <div className={styles.systemMsg}><PartCard part={value as Part} added={name === 'add_part' ? true : ['update_part', 'add_stock', 'apply_review'].includes(name) ? false : undefined} /></div>
  return <div className={styles.activity}>Tool completed.</div>
}
