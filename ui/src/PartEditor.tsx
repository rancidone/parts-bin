import { useState } from 'react'
import type { Part } from './types'
import styles from './PartEditor.module.css'

export function PartEditor({ part, saving, onSave, onCancel }: { part: Part; saving: boolean; onSave: (draft: Part) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(part)
  const [quantity, setQuantity] = useState(String(part.quantity))
  const fields = [
    ['part_category', 'Category'], ['value', 'Value'], ['package', 'Package'],
    ['part_number', 'Part number'], ['manufacturer', 'Manufacturer'],
  ] as const

  return <form className={styles.editor} aria-label={`Edit part #${part.id}`} onSubmit={event => {
    event.preventDefault()
    onSave({ ...draft, quantity: Number(quantity) })
  }}>
    <h3>Edit part #{part.id}</h3>
    <fieldset disabled={saving} className={styles.fields}>
      {fields.map(([key, label], index) => <label key={key}>{label}
        <input autoFocus={index === 0} required={key === 'part_category'} value={draft[key] ?? ''} onChange={event => setDraft({ ...draft, [key]: event.target.value })} />
      </label>)}
      <label>Profile<select value={draft.profile} onChange={event => setDraft({ ...draft, profile: event.target.value })}>
        <option value="passive">Passive</option><option value="discrete_ic">Discrete / IC</option>
      </select></label>
      <label>Quantity<input type="number" min={0} step={1} required value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
      <label>Datasheet link<input type="url" maxLength={2048} placeholder="https://manufacturer.example/datasheet.pdf" value={draft.datasheet_url ?? ''} onChange={event => setDraft({ ...draft, datasheet_url: event.target.value })} /></label>
      <label className={styles.description}>Description<textarea rows={2} value={draft.description ?? ''} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
    </fieldset>
    <div className={styles.actions}>
      <button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
      <button type="button" disabled={saving} onClick={onCancel}>Cancel</button>
    </div>
  </form>
}
