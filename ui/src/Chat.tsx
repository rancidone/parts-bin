import { useEffect, useRef, useState } from 'react'
import { useChat } from './useChat'
import { PartCard } from './PartCard'
import { downloadCSV } from './csv'
import type { Message } from './types'
import styles from './Chat.module.css'

export function Chat() {
  const { messages, send } = useChat()
  const [text, setText] = useState('')
  const [photo, setPhoto] = useState<File | undefined>()
  const [photoPreview, setPhotoPreview] = useState<string | undefined>()
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setPhoto(file)
    setPhotoPreview(URL.createObjectURL(file))
  }

  function clearPhoto() {
    setPhoto(undefined)
    if (photoPreview) URL.revokeObjectURL(photoPreview)
    setPhotoPreview(undefined)
    if (fileRef.current) fileRef.current.value = ''
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!text.trim() && !photo) return
    setSending(true)
    await send(text.trim(), photo)
    setText('')
    clearPhoto()
    setSending(false)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e as unknown as React.FormEvent)
    }
  }

  return (
    <div className={styles.container}>
      <div className={styles.thread}>
        {messages.length === 0 && (
          <div className={styles.empty}>
            Add parts by description or photo, or ask about your inventory.
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} />
        ))}
        {sending && (
          <div className={styles.thinkingBubble}>
            <span className={styles.dot} />
            <span className={styles.dot} />
            <span className={styles.dot} />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form className={styles.inputBar} onSubmit={handleSubmit}>
        {photoPreview && (
          <div className={styles.photoPreview}>
            <img src={photoPreview} alt="attachment" />
            <button type="button" className={styles.removePhoto} onClick={clearPhoto}>✕</button>
          </div>
        )}
        <div className={styles.inputRow}>
          <button
            type="button"
            className={styles.attachBtn}
            onClick={() => fileRef.current?.click()}
            title="Attach photo"
          >
            📎
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            capture="environment"
            onChange={handlePhotoChange}
            style={{ display: 'none' }}
          />
          <textarea
            className={styles.textInput}
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Add a part or ask a question…"
            rows={1}
            disabled={sending}
          />
          <button
            type="submit"
            className={styles.sendBtn}
            disabled={sending || (!text.trim() && !photo)}
          >
            ▶
          </button>
        </div>
      </form>
    </div>
  )
}

function MessageBubble({ msg }: { msg: Message }) {
  if (msg.role === 'user') {
    return (
      <div className={styles.userBubble}>
        {msg.photoUrl && (
          <img className={styles.thumb} src={msg.photoUrl} alt="photo" />
        )}
        {msg.text && <span>{msg.text}</span>}
      </div>
    )
  }

  const m = msg

  if (m.kind === 'ingest-result' && m.part) {
    return (
      <div className={styles.systemMsg}>
        <PartCard part={m.part} added={true} />
      </div>
    )
  }

  if (m.kind === 'chat') {
    return (
      <div className={styles.assistantBubble}>
        {m.text && <span>{m.text}</span>}
        {m.part && <PartCard part={m.part} added={true} />}
      </div>
    )
  }

  if (m.kind === 'clarification' || m.kind === 'not-found' || m.kind === 'text' || m.kind === 'error') {
    return (
      <div className={`${styles.assistantBubble} ${m.kind === 'error' ? styles.errorBubble : ''}`}>
        {m.text}
      </div>
    )
  }

  if (m.kind === 'query-result' && m.matches) {
    return (
      <div className={styles.systemMsg}>
        <div className={styles.queryHeader}>
          <span>{m.matches.length} part{m.matches.length !== 1 ? 's' : ''} found</span>
          <button
            className={styles.exportBtn}
            onClick={() => downloadCSV(m.matches!, 'bom.csv')}
          >
            Export BOM
          </button>
        </div>
        {m.matches.map((p, i) => <PartCard key={i} part={p} />)}
      </div>
    )
  }

  return null
}
