import { useEffect, useMemo, useState } from 'react'
import type { Part } from './types'
import { downloadCSV } from './csv'
import styles from './Inventory.module.css'

type SortKey = 'part_category' | 'value' | 'package' | 'quantity'

interface FieldReview {
  accepted: boolean
  value: string
}

interface PendingReview {
  fields: Record<string, FieldReview>  // key → {accepted, editable value}
  provenance: unknown[]
  outcome: string
}

const DISPLAY_FIELDS: { key: keyof Part; label: string }[] = [
  { key: 'part_category', label: 'Category' },
  { key: 'value', label: 'Value' },
  { key: 'package', label: 'Package' },
  { key: 'quantity', label: 'Qty' },
  { key: 'part_number', label: 'Part #' },
  { key: 'manufacturer', label: 'Manufacturer' },
  { key: 'description', label: 'Description' },
]

export function Inventory({ active }: { active: boolean }) {
  const [parts, setParts] = useState<Part[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('part_category')
  const [sortAsc, setSortAsc] = useState(true)
  const [refreshing, setRefreshing] = useState<Set<number>>(new Set())
  const [pending, setPending] = useState<Map<number, PendingReview>>(new Map())
  const [accepting, setAccepting] = useState<Set<number>>(new Set())

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const resp = await fetch('/inventory')
      if (!resp.ok) throw new Error(resp.statusText)
      setParts(await resp.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { if (active) load() }, [active])

  async function refreshPart(id: number) {
    setRefreshing(prev => new Set(prev).add(id))
    try {
      const resp = await fetch(`/inventory/${id}/refresh`, { method: 'POST' })
      if (!resp.ok) throw new Error(resp.statusText)
      const data = await resp.json()
      const proposed: Record<string, string | number | null> = data.proposed_updates ?? {}
      if (Object.keys(proposed).length > 0) {
        const fields: Record<string, FieldReview> = {}
        for (const [k, v] of Object.entries(proposed)) {
          fields[k] = { accepted: true, value: String(v ?? '') }
        }
        setPending(prev => new Map(prev).set(id, {
          fields,
          provenance: data.provenance,
          outcome: data.outcome,
        }))
      } else {
        setPending(prev => { const next = new Map(prev); next.delete(id); return next })
      }
    } finally {
      setRefreshing(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  function toggleField(id: number, key: string) {
    setPending(prev => {
      const review = prev.get(id)
      if (!review) return prev
      const next = new Map(prev)
      next.set(id, {
        ...review,
        fields: {
          ...review.fields,
          [key]: { ...review.fields[key], accepted: !review.fields[key].accepted },
        },
      })
      return next
    })
  }

  function editField(id: number, key: string, value: string) {
    setPending(prev => {
      const review = prev.get(id)
      if (!review) return prev
      const next = new Map(prev)
      next.set(id, {
        ...review,
        fields: {
          ...review.fields,
          [key]: { ...review.fields[key], value },
        },
      })
      return next
    })
  }

  async function saveReview(id: number) {
    const review = pending.get(id)
    if (!review) return
    const accepted = Object.fromEntries(
      Object.entries(review.fields)
        .filter(([, f]) => f.accepted)
        .map(([k, f]) => [k, f.value])
    )
    if (Object.keys(accepted).length === 0) {
      dismissReview(id)
      return
    }
    setAccepting(prev => new Set(prev).add(id))
    try {
      const resp = await fetch(`/inventory/${id}/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: accepted, provenance: review.provenance }),
      })
      if (!resp.ok) throw new Error(resp.statusText)
      const { part } = await resp.json()
      setParts(prev => prev.map(p => p.id === id ? part : p))
      setPending(prev => { const next = new Map(prev); next.delete(id); return next })
    } finally {
      setAccepting(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  function dismissReview(id: number) {
    setPending(prev => { const next = new Map(prev); next.delete(id); return next })
  }

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(true) }
  }

  const displayed = useMemo(() => {
    const q = filter.toLowerCase()
    const filtered = q
      ? parts.filter(p =>
          [p.part_category, p.value, p.package, p.part_number, p.manufacturer, p.description]
            .some(v => v?.toLowerCase().includes(q))
        )
      : parts

    return [...filtered].sort((a, b) => {
      const av = String(a[sortKey] ?? '')
      const bv = String(b[sortKey] ?? '')
      const cmp = sortKey === 'quantity'
        ? Number(a.quantity) - Number(b.quantity)
        : av.localeCompare(bv)
      return sortAsc ? cmp : -cmp
    })
  }, [parts, filter, sortKey, sortAsc])

  function SortHeader({ col, label }: { col: SortKey; label: string }) {
    const isActive = sortKey === col
    return (
      <th className={`${styles.th} ${isActive ? styles.active : ''}`} onClick={() => toggleSort(col)}>
        {label} {isActive ? (sortAsc ? '↑' : '↓') : ''}
      </th>
    )
  }

  return (
    <div className={styles.container}>
      <div className={styles.toolbar}>
        <input
          className={styles.search}
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder="Filter…"
        />
        <button className={styles.refreshBtn} onClick={load} disabled={loading}>
          {loading ? '…' : '↻'}
        </button>
        <button
          className={styles.exportBtn}
          onClick={() => downloadCSV(displayed, 'inventory.csv')}
          disabled={displayed.length === 0}
        >
          Export CSV
        </button>
      </div>

      {error && <div className={styles.error}>{error}</div>}

      {!loading && displayed.length === 0 && !error && (
        <div className={styles.empty}>No parts found.</div>
      )}

      {displayed.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <SortHeader col="part_category" label="Category" />
                <SortHeader col="value" label="Value" />
                <SortHeader col="package" label="Package" />
                <SortHeader col="quantity" label="Qty" />
                <th className={styles.th}>Part #</th>
                <th className={styles.th}>Manufacturer</th>
                <th className={styles.th}>Description</th>
                <th className={styles.th}></th>
              </tr>
            </thead>
            <tbody>
              {displayed.map((p, i) => {
                const id = p.id!
                const review = p.id != null ? pending.get(id) : undefined
                const acceptedCount = review ? Object.values(review.fields).filter(f => f.accepted).length : 0
                return (
                  <>
                    <tr key={p.id ?? i} className={`${styles.row} ${review ? styles.rowPending : ''}`}>
                      <td className={styles.td}>{p.part_category}</td>
                      <td className={styles.td}>{p.value ?? '—'}</td>
                      <td className={styles.td}>{p.package ?? '—'}</td>
                      <td className={styles.td}>{p.quantity}</td>
                      <td className={styles.td}>{p.part_number ?? '—'}</td>
                      <td className={styles.td}>{p.manufacturer ?? '—'}</td>
                      <td className={styles.td}>{p.description ?? '—'}</td>
                      <td className={styles.tdAction}>
                        {p.part_number && p.id != null && (
                          <button
                            className={styles.rowRefreshBtn}
                            disabled={refreshing.has(id)}
                            onClick={() => refreshPart(id)}
                            title="Fetch specs"
                          >
                            {refreshing.has(id) ? '…' : '↻'}
                          </button>
                        )}
                      </td>
                    </tr>
                    {review && p.id != null && (
                      <tr key={`review-${id}`} className={styles.reviewRow}>
                        <td colSpan={8} className={styles.reviewCell}>
                          <table className={styles.reviewTable}>
                            <tbody>
                              {DISPLAY_FIELDS.filter(f => f.key in review.fields).map(f => {
                                const field = review.fields[f.key]
                                return (
                                  <tr key={f.key} className={field.accepted ? styles.reviewFieldAccepted : styles.reviewFieldRejected}>
                                    <td className={styles.rfCheck}>
                                      <input
                                        type="checkbox"
                                        checked={field.accepted}
                                        onChange={() => toggleField(id, f.key)}
                                      />
                                    </td>
                                    <td className={styles.rfLabel}>{f.label}</td>
                                    <td className={styles.rfOld}>{String(p[f.key] ?? '—')}</td>
                                    <td className={styles.rfArrow}>→</td>
                                    <td className={styles.rfNew}>
                                      <input
                                        className={styles.rfInput}
                                        value={field.value}
                                        disabled={!field.accepted}
                                        onChange={e => editField(id, f.key, e.target.value)}
                                      />
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                          <div className={styles.reviewActions}>
                            <button
                              className={styles.acceptBtn}
                              onClick={() => saveReview(id)}
                              disabled={accepting.has(id) || acceptedCount === 0}
                            >
                              {accepting.has(id) ? '…' : `Save ${acceptedCount} field${acceptedCount !== 1 ? 's' : ''}`}
                            </button>
                            <button
                              className={styles.dismissBtn}
                              onClick={() => dismissReview(id)}
                            >
                              Dismiss
                            </button>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
