import type { Part } from './types'
import styles from './PartCard.module.css'

interface Props {
  part: Part
  added?: boolean
}

export function PartCard({ part, added }: Props) {
  const label = part.profile === 'passive'
    ? [part.part_category, part.value, part.package].filter(Boolean).join(' · ')
    : [part.part_category, part.part_number, part.package].filter(Boolean).join(' · ')

  return (
    <div className={styles.card}>
      <div className={styles.label}>{label}</div>
      {part.manufacturer && <div className={styles.sub}>{part.manufacturer}</div>}
      {part.description && <div className={styles.desc}>{part.description}</div>}
      <div className={styles.footer}>
        <span className={styles.qty}>Qty: {part.quantity}</span>
        {added !== undefined && (
          <span className={styles.badge}>{added ? 'Added' : 'Updated'}</span>
        )}
      </div>
    </div>
  )
}
