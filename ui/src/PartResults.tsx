import { useState } from 'react'
import { useQuantities } from './quantityContext'
import { PartCard } from './PartCard'
import type { Part } from './types'
import styles from './PartResults.module.css'

const PAGE_SIZE = 10

export function PartResults({ parts, count, truncated, onOpenPart }: { parts: Part[]; count?: number; truncated?: boolean; onOpenPart?: (id: number) => void }) {
  const { quantities } = useQuantities()
  const [page, setPage] = useState(0)
  const total = Math.max(parts.length, count ?? parts.length)
  const partial = truncated || total > parts.length
  const categories = new Map<string, number>()
  for (const part of parts) categories.set(part.part_category, (categories.get(part.part_category) ?? 0) + 1)
  const start = page * PAGE_SIZE
  const visible = parts.slice(start, start + PAGE_SIZE)

  if (total === 0) return <div className={styles.empty}>No matching parts found.</div>
  if (total === 1 && parts.length === 1 && !partial) return <PartCard part={parts[0]} onOpenPart={onOpenPart} />

  return <details className={styles.group} open={total <= 5}>
    <summary className={styles.summary}>
      <strong>{total} matching inventory records</strong>
      <span className={styles.overview}>{partial ? 'Returned: ' : ''}{[...categories].slice(0, 3).map(([category, amount]) => `${category}: ${amount}`).join(' · ')}{categories.size > 3 ? ` · +${categories.size - 3} categories` : ''}</span>
      <span className={styles.hint}>Expand to browse parts</span>
    </summary>
    <div className={styles.content}>
      {partial && <p className={styles.notice}>{parts.length} of {total} records returned. Narrow your search to find other matches.</p>}
      <div className={styles.categories} aria-label={partial ? 'Categories in returned records' : 'Categories in matching records'}>
        {partial && <span>Returned categories:</span>}
        {[...categories].map(([category, amount]) => <span key={category} className={styles.category}>{category}: {amount}</span>)}
      </div>
      <ul className={styles.parts}>
        {visible.map((part, index) => <li key={part.id ?? start + index}>
          <details className={styles.part}>
            <summary className={styles.row}>
              <span className={styles.identity}>{part.id != null && onOpenPart
                ? <button className={styles.partLink} onClick={event => { event.preventDefault(); onOpenPart(part.id!) }} aria-label={`View part #${part.id} in inventory`}>{part.profile === 'passive' ? [part.part_category, part.value].filter(Boolean).join(' · ') : part.part_number || part.part_category}</button>
                : part.profile === 'passive' ? [part.part_category, part.value].filter(Boolean).join(' · ') : part.part_number || part.part_category}</span>
              <span className={styles.package}>{part.package || 'Package unknown'}</span>
              <span className={styles.quantity}>Qty: {(part.id != null ? quantities.get(part.id) : undefined) ?? part.quantity}</span>
            </summary>
            <div className={styles.partDetail}><PartCard part={part} onOpenPart={onOpenPart} /></div>
          </details>
        </li>)}
      </ul>
      {parts.length > PAGE_SIZE && <nav className={styles.pagination} aria-label="Part result pages">
        <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
        <span aria-live="polite">{start + 1}–{Math.min(start + PAGE_SIZE, parts.length)} of {parts.length} returned</span>
        <button type="button" disabled={start + PAGE_SIZE >= parts.length} onClick={() => setPage(page + 1)}>Next</button>
      </nav>}
    </div>
  </details>
}
