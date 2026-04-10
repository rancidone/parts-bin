import { useEffect, useMemo, useState } from 'react'
import { FieldReviewEditor } from './FieldReviewEditor'
import { downloadCSV } from './csv'
import type { FieldProvenance, FieldReview, Part, PendingReview } from './types'
import styles from './Inventory.module.css'

type SortKey = 'part_category' | 'value' | 'package' | 'quantity'

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
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<Part | null>(null)
  const [savingEdit, setSavingEdit] = useState(false)
  const [deleting, setDeleting] = useState<Set<number>>(new Set())
  const [rowStatus, setRowStatus] = useState<Map<number, string>>(new Map())
  const [expandedProvenance, setExpandedProvenance] = useState<Set<number>>(new Set())
  const [loadingProvenance, setLoadingProvenance] = useState<Set<number>>(new Set())
  const [provenanceByPart, setProvenanceByPart] = useState<Map<number, FieldProvenance[]>>(new Map())

  function toDraft(part: Part): Part {
    return {
      ...part,
      value: part.value ?? '',
      package: part.package ?? '',
      part_number: part.part_number ?? '',
      manufacturer: part.manufacturer ?? '',
      description: part.description ?? '',
    }
  }

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const [partsResp, pendingResp] = await Promise.all([
        fetch('/inventory'),
        fetch('/inventory/pending'),
      ])
      if (!partsResp.ok) throw new Error(partsResp.statusText)
      if (!pendingResp.ok) throw new Error(pendingResp.statusText)
      setParts(await partsResp.json())
      const pendingData = await pendingResp.json()
      setPending(new Map(
        Object.entries((pendingData.reviews ?? {}) as Record<string, PendingReview>)
          .map(([id, review]) => [Number(id), review]),
      ))
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
        setRowStatus(prev => new Map(prev).set(id, refreshStatusMessage(data.outcome, true)))
      } else {
        setPending(prev => { const next = new Map(prev); next.delete(id); return next })
        setRowStatus(prev => new Map(prev).set(id, refreshStatusMessage(data.outcome, false)))
      }
    } catch (e) {
      setRowStatus(prev => new Map(prev).set(id, String(e)))
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
      setRowStatus(prev => new Map(prev).set(id, 'Saved accepted review fields.'))
    } finally {
      setAccepting(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  async function dismissReview(id: number) {
    try {
      await fetch(`/inventory/${id}/dismiss`, { method: 'POST' })
    } finally {
      setPending(prev => { const next = new Map(prev); next.delete(id); return next })
      setRowStatus(prev => new Map(prev).set(id, 'Dismissed pending review.'))
    }
  }

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(true) }
  }

  function beginEdit(part: Part) {
    setEditing(part.id ?? null)
    setDraft(toDraft(part))
    setError(null)
  }

  function cancelEdit() {
    setEditing(null)
    setDraft(null)
  }

  function updateDraft<K extends keyof Part>(key: K, value: Part[K]) {
    setDraft(prev => prev ? { ...prev, [key]: value } : prev)
  }

  async function saveEdit(id: number) {
    if (!draft) return
    setSavingEdit(true)
    setError(null)
    try {
      const payload = {
        ...draft,
        part_category: draft.part_category.trim(),
        profile: draft.profile.trim(),
        value: draft.value?.trim() || null,
        package: draft.package?.trim() || null,
        part_number: draft.part_number?.trim() || null,
        manufacturer: draft.manufacturer?.trim() || null,
        description: draft.description?.trim() || null,
        quantity: Number.isFinite(Number(draft.quantity)) ? Number(draft.quantity) : draft.quantity,
      }
      const resp = await fetch(`/inventory/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ part: payload }),
      })
      if (!resp.ok) {
        const detail = await resp.text().catch(() => resp.statusText)
        throw new Error(detail)
      }
      const { part } = await resp.json()
      setParts(prev => prev.map(p => p.id === id ? part : p))
      setPending(prev => { const next = new Map(prev); next.delete(id); return next })
      setRowStatus(prev => new Map(prev).set(id, 'Saved manual edit.'))
      cancelEdit()
    } catch (e) {
      setError(String(e))
    } finally {
      setSavingEdit(false)
    }
  }

  async function deleteRow(id: number) {
    const part = parts.find(p => p.id === id)
    const label = part?.part_category ?? 'part'
    if (!window.confirm(`Delete this ${label}?`)) return

    setDeleting(prev => new Set(prev).add(id))
    setError(null)
    try {
      const resp = await fetch(`/inventory/${id}`, { method: 'DELETE' })
      if (!resp.ok) {
        const detail = await resp.text().catch(() => resp.statusText)
        throw new Error(detail)
      }
      setParts(prev => prev.filter(p => p.id !== id))
      setPending(prev => {
        const next = new Map(prev)
        next.delete(id)
        return next
      })
      setProvenanceByPart(prev => {
        const next = new Map(prev)
        next.delete(id)
        return next
      })
      setExpandedProvenance(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      setRowStatus(prev => { const next = new Map(prev); next.delete(id); return next })
      if (editing === id) cancelEdit()
    } catch (e) {
      setError(String(e))
    } finally {
      setDeleting(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
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

  function refreshStatusMessage(outcome: string, hasProposals: boolean): string {
    if (hasProposals) return 'Fetched proposed source-backed updates for review.'
    switch (outcome) {
      case 'incomplete':
        return 'Lookup finished, but it did not expose any new writable metadata.'
      case 'no_match':
        return 'No acceptable source match was found for this part.'
      case 'conflict':
        return 'High-authority sources conflicted, so no automatic proposal was created.'
      case 'timeout':
        return 'Lookup timed out before enough metadata could be verified.'
      case 'failed':
        return 'Lookup failed before enough metadata could be verified.'
      case 'saved':
        return 'Lookup succeeded, but it did not change any writable fields.'
      default:
        return `Lookup finished with outcome: ${outcome}.`
    }
  }

  async function toggleProvenance(id: number) {
    if (expandedProvenance.has(id)) {
      setExpandedProvenance(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      return
    }

    if (!provenanceByPart.has(id)) {
      setLoadingProvenance(prev => new Set(prev).add(id))
      try {
        const resp = await fetch(`/inventory/${id}/provenance`)
        if (!resp.ok) throw new Error(resp.statusText)
        const data = await resp.json()
        setProvenanceByPart(prev => new Map(prev).set(id, data.provenance ?? []))
      } catch (e) {
        setRowStatus(prev => new Map(prev).set(id, `Could not load provenance: ${String(e)}`))
      } finally {
        setLoadingProvenance(prev => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      }
    }

    setExpandedProvenance(prev => new Set(prev).add(id))
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
                const status = p.id != null ? rowStatus.get(id) : undefined
                const provenance = p.id != null ? provenanceByPart.get(id) : undefined
                const showProvenance = p.id != null && expandedProvenance.has(id)
                const isEditing = editing === id && draft != null
                return (
                  <>
                    <tr key={p.id ?? i} className={`${styles.row} ${review ? styles.rowPending : ''}`}>
                      <td className={styles.td}>
                        {isEditing ? (
                          <input
                            className={styles.cellInput}
                            value={draft.part_category}
                            onChange={e => updateDraft('part_category', e.target.value)}
                          />
                        ) : p.part_category}
                      </td>
                      <td className={styles.td}>
                        {isEditing ? (
                          <input
                            className={styles.cellInput}
                            value={draft.value ?? ''}
                            onChange={e => updateDraft('value', e.target.value)}
                          />
                        ) : (p.value ?? '—')}
                      </td>
                      <td className={styles.td}>
                        {isEditing ? (
                          <input
                            className={styles.cellInput}
                            value={draft.package ?? ''}
                            onChange={e => updateDraft('package', e.target.value)}
                          />
                        ) : (p.package ?? '—')}
                      </td>
                      <td className={styles.td}>
                        {isEditing ? (
                          <input
                            className={styles.cellInput}
                            type="number"
                            min={0}
                            value={draft.quantity}
                            onChange={e => updateDraft('quantity', Number(e.target.value))}
                          />
                        ) : p.quantity}
                      </td>
                      <td className={styles.td}>
                        {isEditing ? (
                          <input
                            className={styles.cellInput}
                            value={draft.part_number ?? ''}
                            onChange={e => updateDraft('part_number', e.target.value)}
                          />
                        ) : (p.part_number ?? '—')}
                      </td>
                      <td className={styles.td}>
                        {isEditing ? (
                          <input
                            className={styles.cellInput}
                            value={draft.manufacturer ?? ''}
                            onChange={e => updateDraft('manufacturer', e.target.value)}
                          />
                        ) : (p.manufacturer ?? '—')}
                      </td>
                      <td className={styles.td}>
                        {isEditing ? (
                          <div className={styles.editDescription}>
                            <select
                              className={styles.cellInput}
                              value={draft.profile}
                              onChange={e => updateDraft('profile', e.target.value)}
                            >
                              <option value="passive">passive</option>
                              <option value="discrete_ic">discrete_ic</option>
                            </select>
                            <input
                              className={styles.cellInput}
                              value={draft.description ?? ''}
                              onChange={e => updateDraft('description', e.target.value)}
                            />
                          </div>
                        ) : (p.description ?? '—')}
                      </td>
                      <td className={styles.tdAction}>
                        <div className={styles.actionGroup}>
                          {isEditing ? (
                            <>
                              <button
                                className={styles.rowIconBtn}
                                disabled={savingEdit}
                                onClick={() => saveEdit(id)}
                                title="Save edit"
                                aria-label="Save edit"
                              >
                                {savingEdit ? '…' : '✓'}
                              </button>
                              <button
                                className={styles.rowIconBtn}
                                disabled={savingEdit}
                                onClick={cancelEdit}
                                title="Cancel edit"
                                aria-label="Cancel edit"
                              >
                                ×
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                className={styles.rowIconBtn}
                                onClick={() => beginEdit(p)}
                                title="Edit part"
                                aria-label="Edit part"
                              >
                                ✎
                              </button>
                              <button
                                className={`${styles.rowIconBtn} ${styles.rowDeleteBtn}`}
                                disabled={deleting.has(id)}
                                onClick={() => deleteRow(id)}
                                title="Delete part"
                                aria-label="Delete part"
                              >
                                {deleting.has(id) ? '…' : '🗑'}
                              </button>
                              <button
                                className={styles.rowIconBtn}
                                disabled={loadingProvenance.has(id)}
                                onClick={() => toggleProvenance(id)}
                                title="Show provenance"
                                aria-label="Show provenance"
                              >
                                {loadingProvenance.has(id) ? '…' : (showProvenance ? '⊟' : '⊞')}
                              </button>
                              {p.part_number && p.id != null && (
                                <button
                                  className={styles.rowRefreshBtn}
                                  disabled={refreshing.has(id)}
                                  onClick={() => refreshPart(id)}
                                  title="Fetch specs"
                                  aria-label="Fetch specs"
                                >
                                  {refreshing.has(id) ? '…' : '↻'}
                                </button>
                              )}
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                    {review && p.id != null && !isEditing && (
                      <FieldReviewEditor
                        key={`review-${id}`}
                        part={p}
                        review={review}
                        colSpan={8}
                        saving={accepting.has(id)}
                        onToggleField={key => toggleField(id, key)}
                        onEditField={(key, value) => editField(id, key, value)}
                        onSave={() => saveReview(id)}
                        onDismiss={() => dismissReview(id)}
                      />
                    )}
                    {!review && status && p.id != null && !isEditing && (
                      <tr key={`status-${id}`} className={styles.statusRow}>
                        <td colSpan={8} className={styles.statusCell}>
                          {status}
                        </td>
                      </tr>
                    )}
                    {showProvenance && p.id != null && !isEditing && (
                      <tr key={`provenance-${id}`} className={styles.provenanceRow}>
                        <td colSpan={8} className={styles.provenanceCell}>
                          {provenance && provenance.length > 0 ? (
                            <div className={styles.provenanceList}>
                              {provenance.map((entry, index) => (
                                <div key={`${entry.field_name}-${index}`} className={styles.provenanceItem}>
                                  <div className={styles.provenanceHeader}>
                                    <span className={styles.provenanceField}>{entry.field_name}</span>
                                    <span className={styles.provenanceValue}>{entry.field_value ?? '—'}</span>
                                  </div>
                                  <div className={styles.provenanceMeta}>
                                    <span>{entry.source_tier ?? 'unknown-tier'}</span>
                                    <span>{entry.source_kind ?? 'unknown-source'}</span>
                                    <span>{entry.extraction_method ?? 'unknown-method'}</span>
                                    {entry.confidence_marker && <span>{entry.confidence_marker}</span>}
                                  </div>
                                  {entry.source_locator && (
                                    <div className={styles.provenanceLocator} title={entry.source_locator}>
                                      {entry.source_locator}
                                    </div>
                                  )}
                                  {entry.evidence && (
                                    <div className={styles.provenanceEvidence}>
                                      {entry.evidence}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className={styles.provenanceEmpty}>
                              No accepted provenance is stored for this part yet.
                            </div>
                          )}
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
