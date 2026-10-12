import { useEffect, useRef, useState } from 'react'
import type { Part } from './types'
import styles from './BulkEditor.module.css'

const fields = [
  ['part_category', 'Category'], ['profile', 'Profile'], ['value', 'Value'],
  ['package', 'Package'], ['part_number', 'Part number'], ['manufacturer', 'Manufacturer'],
  ['quantity', 'Quantity'], ['datasheet_url', 'Datasheet link'], ['description', 'Description'],
] as const

type Review = { request_id: string; changes: { before: Part; after: Part }[]; cleared_reviews: number[] }

class RequestError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

async function post(path: string, body: object) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const result = await response.json()
  if (!response.ok) throw new RequestError(typeof result.detail === 'string' ? result.detail : result.detail?.message ?? 'Could not save changes.', response.status)
  return result
}

export function BulkEditor({ parts, onSaved, onClose }: { parts: Part[]; onSaved: (parts: Part[]) => void; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [enabled, setEnabled] = useState<Set<string>>(new Set())
  const [values, setValues] = useState<Record<string, string>>({ profile: 'passive' })
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { dialog.current?.showModal() }, [])

  async function preview() {
    setBusy(true)
    setError('')
    try {
      const updates = Object.fromEntries([...enabled].map(key => [key, key === 'quantity' ? Number(values[key]) : values[key]?.trim() || null]))
      setReview(await post('/inventory/bulk/preview', { part_ids: parts.map(part => part.id), fields: updates }))
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  async function decide(approved: boolean, close = false) {
    if (!review) { onClose(); return }
    setBusy(true)
    setError('')
    try {
      const result = await post('/inventory/bulk/decide', { request_id: review.request_id, approved })
      if (approved) onSaved(result.parts)
      else if (close) onClose()
      else setReview(null)
    } catch (e) {
      setError(String(e))
      if (e instanceof RequestError && e.status === 409) setReview(null)
    } finally { setBusy(false) }
  }

  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="bulk-title" onCancel={event => { event.preventDefault(); if (!busy) void decide(false, true) }}>
    <h2 id="bulk-title">Edit {parts.length} selected parts</h2>
    {error && <p role="alert">{error}</p>}
    {review ? <>
      <p>Review all changes before approving. Blank values clear a field. Quantity is the new count for each part.</p>
      {review.cleared_reviews.length > 0 && <p>Saving will dismiss pending metadata reviews for {review.cleared_reviews.length} selected parts.</p>}
      <div className={styles.review}>{review.changes.map(({ before, after }) => <section key={before.id}>
        <h3>#{before.id} · {before.part_number ?? before.value ?? before.part_category}</h3>
        <table><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>
          {fields.filter(([key]) => before[key] !== after[key]).map(([key, label]) => <tr key={key}><th>{label}</th><td>{before[key] ?? '—'}</td><td>{after[key] ?? '—'}</td></tr>)}
        </tbody></table>
        {fields.every(([key]) => before[key] === after[key]) && <p>No field changes.</p>}
      </section>)}</div>
      <div className={styles.actions}>
        <button disabled={busy} onClick={() => void decide(true)}>{busy ? 'Saving…' : 'Approve changes'}</button>
        <button disabled={busy} onClick={() => void decide(false)}>Back to editing</button>
        <button disabled={busy} onClick={() => void decide(false, true)}>Cancel</button>
      </div>
    </> : <form onSubmit={event => { event.preventDefault(); void preview() }}>
      <p>Only checked fields change. Unchecked fields keep their current values. Blank values clear a checked field.</p>
      <p>Selected: {parts.map(part => `#${part.id}`).join(', ')}</p>
      <fieldset disabled={busy} className={styles.fields}>
        {fields.map(([key, label]) => <div key={key}>
          <label><input type="checkbox" checked={enabled.has(key)} onChange={event => setEnabled(previous => { const next = new Set(previous); if (event.target.checked) next.add(key); else next.delete(key); return next })} />Change {label}</label>
          <label>{label}{key === 'profile' ? <select disabled={!enabled.has(key)} value={values[key]} onChange={event => setValues({ ...values, [key]: event.target.value })}><option value="passive">Passive</option><option value="discrete_ic">Discrete / IC</option></select> : <input disabled={!enabled.has(key)} required={enabled.has(key) && (key === 'quantity' || key === 'part_category')} type={key === 'quantity' ? 'number' : key === 'datasheet_url' ? 'url' : 'text'} min={key === 'quantity' ? 0 : undefined} step={key === 'quantity' ? 1 : undefined} value={values[key] ?? ''} onChange={event => setValues({ ...values, [key]: event.target.value })} />}</label>
        </div>)}
      </fieldset>
      <p>Quantity sets the same count on every selected part.</p>
      <div className={styles.actions}><button disabled={busy || enabled.size === 0} type="submit">{busy ? 'Preparing…' : 'Review changes'}</button><button disabled={busy} type="button" onClick={onClose}>Cancel</button></div>
    </form>}
  </dialog>
}
