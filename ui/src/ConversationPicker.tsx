import { useEffect, useId, useRef, useState } from 'react'
import type { ConversationSummary } from './types'
import styles from './ConversationPicker.module.css'

export function ConversationPicker({ conversations, threadId, disabled, onSelect }: {
  conversations: ConversationSummary[]
  threadId: string | null
  disabled: boolean
  onSelect: (threadId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const container = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const panelId = useId()
  const selected = conversations.find(item => item.thread_id === threadId)
  const filtered = conversations.filter(item => `${item.title} ${item.runtime}`.toLowerCase().includes(query.trim().toLowerCase()))

  useEffect(() => {
    if (!open) return
    search.current?.focus()
    function dismiss(event: PointerEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    return () => document.removeEventListener('pointerdown', dismiss)
  }, [open])

  function select(id: string) {
    setOpen(false)
    trigger.current?.focus()
    onSelect(id)
  }

  return <div ref={container} className={styles.picker} onKeyDown={event => {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      trigger.current?.focus()
    }
  }} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
  }}>
    <button ref={trigger} className={styles.trigger} aria-label="Conversation" aria-expanded={open && !disabled}
      aria-controls={panelId} disabled={disabled} title={selected?.title}
      onClick={() => { setQuery(''); setOpen(!open) }}>
      <span>{selected?.title ?? (threadId ? 'Conversation' : 'New conversation')}</span>
      <span aria-hidden="true">▾</span>
    </button>
    {open && !disabled && <div id={panelId} role="region" aria-label="Conversation history" className={styles.panel}>
      <input ref={search} type="search" aria-label="Search conversations" placeholder="Search conversations…"
        className={styles.search} value={query} onChange={event => setQuery(event.target.value)} />
      <button className={styles.newChat} onClick={() => select('')}>+ New conversation</button>
      <div className={styles.list}>
        {filtered.map(item => <button key={item.thread_id} className={styles.item}
          aria-current={item.thread_id === threadId ? 'true' : undefined} onClick={() => select(item.thread_id)}>
          <span className={styles.title}>{item.title}</span>
          <span className={styles.provider}>{item.runtime === 'openai' ? 'OpenAI' : `${item.runtime} · Read-only`}</span>
        </button>)}
        {!filtered.length && <p className={styles.empty}>{query.trim() ? 'No matching conversations.' : 'No conversations yet.'}</p>}
      </div>
      <div className={styles.count}>{filtered.length} of {conversations.length} conversations</div>
    </div>}
  </div>
}
