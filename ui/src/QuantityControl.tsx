import { useQuantities } from './quantityContext'
import type { Part } from './types'
import styles from './QuantityControl.module.css'

export function QuantityControl({ part, disabled = false }: { part: Part; disabled?: boolean }) {
  const { quantities, busy, errors, adjust } = useQuantities()
  const id = part.id
  const quantity = id == null ? part.quantity : quantities.get(id) ?? part.quantity
  const saving = id != null && busy.has(id)
  const error = id != null ? errors.get(id) : undefined
  const label = `${part.part_number || [part.part_category, part.value].filter(Boolean).join(' ')}${id != null ? ` (#${id})` : ''}`

  return <div className={styles.container}>
    <div className={styles.control} role="group" aria-label={`Quantity for ${label}`} aria-busy={saving}>
      {id != null && <button type="button" aria-label={`Decrease quantity for ${label}`} disabled={disabled || saving || quantity <= 0} onClick={() => void adjust(id, -1)}>−</button>}
      <span className={styles.quantity} aria-live="polite">Qty: {quantity}</span>
      {id != null && <button type="button" aria-label={`Increase quantity for ${label}`} disabled={disabled || saving} onClick={() => void adjust(id, 1)}>+</button>}
    </div>
    {error && <div className={styles.error} role="alert">{error}</div>}
  </div>
}
