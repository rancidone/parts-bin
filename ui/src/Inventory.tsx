import { useEffect, useMemo, useState } from 'react'
import type { Part } from './types'
import { downloadCSV } from './csv'
import styles from './Inventory.module.css'

type SortKey = 'part_category' | 'value' | 'package' | 'quantity'

export function Inventory({ active }: { active: boolean }) {
  const [parts, setParts] = useState<Part[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('part_category')
  const [sortAsc, setSortAsc] = useState(true)

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
    const active = sortKey === col
    return (
      <th className={`${styles.th} ${active ? styles.active : ''}`} onClick={() => toggleSort(col)}>
        {label} {active ? (sortAsc ? '↑' : '↓') : ''}
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
              </tr>
            </thead>
            <tbody>
              {displayed.map((p, i) => (
                <tr key={p.id ?? i} className={styles.row}>
                  <td className={styles.td}>{p.part_category}</td>
                  <td className={styles.td}>{p.value ?? '—'}</td>
                  <td className={styles.td}>{p.package ?? '—'}</td>
                  <td className={styles.td}>{p.quantity}</td>
                  <td className={styles.td}>{p.part_number ?? '—'}</td>
                  <td className={styles.td}>{p.manufacturer ?? '—'}</td>
                  <td className={styles.td}>{p.description ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
