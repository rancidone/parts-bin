import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { INVENTORY_PAGE_SIZE } from './inventorySearchUrl'
import { useInventorySearchUrl } from './useInventorySearchUrl'
import { useInventorySearch } from './useInventorySearch'
import { PartEditor } from './PartEditor'
import { BulkEditor } from './BulkEditor'
import { QuantityControl } from './QuantityControl'
import { useQuantities } from './quantityContext'
import { FieldReviewEditor } from './FieldReviewEditor'
import { SpecificationResult } from './SpecificationFacts'
import { SpecificationSearch } from './SpecificationSearch'
import type { SpecificationState } from './SpecificationFacts'
import { downloadCSV } from './csv'
import type { FieldProvenance, FieldReview, Part, PendingReview } from './types'
import styles from './Inventory.module.css'

type SortKey = 'part_category' | 'value' | 'package' | 'quantity'

export function Inventory({ active, selectedPartId, onClearSelection }: { active: boolean; selectedPartId: number | null; onClearSelection: () => void }) {
  const { quantities, busy: adjustingQuantity, remember } = useQuantities()
  const [parts, setParts] = useState<Part[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const searchUrl = useInventorySearchUrl()
  const { query: filter, category, minimumStock, page: searchPage } = searchUrl
  const searchOffset = (searchPage - 1) * INVENTORY_PAGE_SIZE
  const [searchElectrical, setSearchElectrical] = useState(false)
  const electricalMode = searchElectrical && selectedPartId === null
  const [sortKey, setSortKey] = useState<SortKey>('part_category')
  const [sortAsc, setSortAsc] = useState(true)
  const [refreshing, setRefreshing] = useState<Set<number>>(new Set())
  const [pending, setPending] = useState<Map<number, PendingReview>>(new Map())
  const [accepting, setAccepting] = useState<Set<number>>(new Set())
  const [editing, setEditing] = useState<number | null>(null)
  const [bulkIds, setBulkIds] = useState<Set<number>>(new Set())
  const [bulkEditing, setBulkEditing] = useState(false)
  const [savingEdit, setSavingEdit] = useState(false)
  const [deleting, setDeleting] = useState<Set<number>>(new Set())
  const [rowStatus, setRowStatus] = useState<Map<number, string>>(new Map())
  const [electrical, setElectrical] = useState<Map<number, SpecificationState>>(new Map())
  const [expandedElectrical, setExpandedElectrical] = useState<Set<number>>(new Set())
  const [expandedProvenance, setExpandedProvenance] = useState<Set<number>>(new Set())
  const [loadingProvenance, setLoadingProvenance] = useState<Set<number>>(new Set())
  const [provenanceByPart, setProvenanceByPart] = useState<Map<number, FieldProvenance[]>>(new Map())

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [partsResp, pendingResp] = await Promise.all([
        fetch('/inventory'),
        fetch('/inventory/pending'),
      ])
      if (!partsResp.ok) throw new Error(partsResp.statusText)
      if (!pendingResp.ok) throw new Error(pendingResp.statusText)
      const loadedParts: Part[] = await partsResp.json()
      setParts(loadedParts)
      setBulkIds(new Set())
      remember(loadedParts)
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
  }, [remember])

  useEffect(() => { if (active) void load() }, [active, load])

  async function refreshPart(id: number) {
    setRefreshing(prev => new Set(prev).add(id))
    try {
      const resp = await fetch(`/inventory/${id}/refresh`, { method: 'POST' })
      if (!resp.ok) throw new Error(resp.statusText)
      const data = await resp.json()
      if (data.electrical) {
        setElectrical(prev => new Map(prev).set(id, data.electrical))
        setExpandedElectrical(prev => new Set(prev).add(id))
      }
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
        const candidates = (data.lookup_candidates ?? []) as { part_number: string; manufacturer: string | null }[]
        const message = data.outcome === 'source_refresh' ? 'Datasheet refresh finished. Review the electrical results below.' : refreshStatusMessage(data.outcome, false)
        const choices = candidates.map(candidate =>
          `${candidate.part_number}${candidate.manufacturer ? ` (${candidate.manufacturer})` : ''}`,
        ).join(', ')
        setRowStatus(prev => new Map(prev).set(id, choices ? `${message} Search candidates: ${choices}.` : message))
      }
    } catch (e) {
      setRowStatus(prev => new Map(prev).set(id, String(e)))
    } finally {
      setRefreshing(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  async function showElectrical(id: number) {
    if (expandedElectrical.has(id)) {
      setExpandedElectrical(prev => { const next = new Set(prev); next.delete(id); return next })
      return
    }
    try {
      const response = await fetch(`/inventory/${id}/specifications`)
      if (!response.ok) throw new Error(response.statusText)
      const data: SpecificationState = await response.json()
      setElectrical(prev => new Map(prev).set(id, data))
      setExpandedElectrical(prev => new Set(prev).add(id))
    } catch (e) {
      setRowStatus(prev => new Map(prev).set(id, String(e)))
    }
  }

  async function decideElectrical(id: number, approved: boolean) {
    const review = electrical.get(id)?.pending_review
    if (!review) return
    setAccepting(prev => new Set(prev).add(id))
    try {
      const response = await fetch(`/inventory/${id}/specifications/decide`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review, approved }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail?.message ?? data.detail ?? response.statusText)
      setElectrical(prev => new Map(prev).set(id, data))
      setRowStatus(prev => new Map(prev).set(id, approved ? 'Accepted electrical specifications.' : 'Dismissed electrical review.'))
    } catch (e) {
      setRowStatus(prev => new Map(prev).set(id, String(e)))
    } finally {
      setAccepting(prev => { const next = new Set(prev); next.delete(id); return next })
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
      remember([part])
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
    setError(null)
  }

  function cancelEdit() {
    setEditing(null)
  }

  async function saveEdit(id: number, draft: Part) {
    setSavingEdit(true)
    setError(null)
    try {
      // Send only domain-editable fields. The API intentionally rejects
      // identity and audit columns such as id/created_at/updated_at.
      const payload = {
        part_category: draft.part_category.trim(),
        profile: draft.profile.trim(),
        value: draft.value?.trim() || null,
        package: draft.package?.trim() || null,
        part_number: draft.part_number?.trim() || null,
        manufacturer: draft.manufacturer?.trim() || null,
        description: draft.description?.trim() || null,
        datasheet_url: draft.datasheet_url?.trim() || null,
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
      remember([part])
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
      setBulkIds(prev => { const next = new Set(prev); next.delete(id); return next })
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

  const searching = selectedPartId === null && !electricalMode && Boolean(filter.trim() || category || minimumStock !== '0' || searchPage > 1)
  const search = useInventorySearch(active && searching, filter, category, minimumStock, searchOffset, parts, quantities)
  const categories = [...new Set([...parts.map(part => part.part_category), ...(category ? [category] : [])])].sort()
  function resetSearch() { searchUrl.update({ query: '', category: '', minimumStock: '0', page: 1 }) }

  const displayed = useMemo(() => {
    const source = searching ? (search.page?.candidates ?? []) : parts
    const currentParts = source.map(part => ({ ...part, quantity: (!searching && part.id != null ? quantities.get(part.id) : undefined) ?? part.quantity }))
    const filtered = selectedPartId !== null ? currentParts.filter(p => p.id === selectedPartId) : currentParts

    return [...filtered].sort((a, b) => {
      const av = String(a[sortKey] ?? '')
      const bv = String(b[sortKey] ?? '')
      const cmp = sortKey === 'quantity'
        ? Number(a.quantity) - Number(b.quantity)
        : av.localeCompare(bv)
      return sortAsc ? cmp : -cmp
    })
  }, [parts, searching, search.page, sortKey, sortAsc, selectedPartId, quantities])

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
      case 'needs_clarification':
        return 'Supplier search found possible matches. Confirm the full part marking and manufacturer, then edit the part number and retry.'
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
        <button className={styles.refreshBtn} aria-pressed={electricalMode} onClick={() => { if (selectedPartId !== null) onClearSelection(); setSearchElectrical(!electricalMode) }}>{electricalMode ? 'Browse inventory' : 'Filter electrical specs'}</button>
        <input
          className={styles.search}
          disabled={electricalMode}
          aria-label="Filter inventory"
          value={filter}
          onChange={e => searchUrl.update({ query: e.target.value, page: 1 }, 'query')}
          onBlur={searchUrl.finishTyping}
          maxLength={200}
          placeholder="Filter…"
        />
        <button className={styles.refreshBtn} onClick={load} disabled={loading}>
          {loading ? '…' : '↻'}
        </button>
        <button
          className={styles.exportBtn}
          onClick={() => downloadCSV(displayed, 'inventory.csv')}
          disabled={electricalMode || displayed.length === 0 || (searching && search.pending)}
        >
          Export CSV
        </button>
      </div>

      {electricalMode ? <SpecificationSearch key={loading ? 'loading' : 'ready'} /> : <>

      {selectedPartId === null && <div className={styles.searchFilters}>
        <label>Category<select aria-label="Search category" value={category} onChange={event => searchUrl.update({ category: event.target.value, page: 1 })}><option value="">All categories</option>{categories.map(name => <option key={name} value={name}>{name.replaceAll('_', ' ')}</option>)}</select></label>
        <label>Minimum stock<input aria-label="Minimum stock" type="number" min="0" step="1" value={minimumStock} onChange={event => searchUrl.update({ minimumStock: event.target.value, page: 1 }, 'stock')} onBlur={searchUrl.finishTyping} /></label>
        <button className={styles.refreshBtn} onClick={resetSearch}>Clear filters</button>
        <span>Combine part, manufacturer and package terms. Stock applies to each record.</span>
      </div>}
      {searching && <div className={styles.selection} role="status">
        {search.pending ? 'Searching inventory…' : search.error ? 'Search unavailable.' : `${search.page?.count ?? 0} ${filter.trim() ? 'candidate' : 'inventory'} record${search.page?.count === 1 ? '' : 's'} · showing ${displayed.length ? searchOffset + 1 : 0}–${displayed.length ? searchOffset + displayed.length : 0}`}
        {!search.pending && !search.error && filter.trim() && <span>Candidates do not establish interchangeable parts or electrical suitability.</span>}
        <button className={styles.refreshBtn} disabled={search.pending || searchOffset === 0} onClick={() => searchUrl.update({ page: Math.max(1, searchPage - 1) })}>Previous results</button>
        <button className={styles.refreshBtn} disabled={search.pending || search.error !== null || search.page?.next_offset == null} onClick={() => searchUrl.update({ page: search.page!.next_offset! / INVENTORY_PAGE_SIZE + 1 })}>Next results</button>
      </div>}
      {searching && !search.pending && !search.error && search.page && searchOffset > 0 && searchOffset >= search.page.count && <div className={styles.selection}>
        <span>Page {searchPage} is no longer available for these results.</span>
        <button className={styles.refreshBtn} onClick={() => searchUrl.update({ page: 1 })}>First results</button>
      </div>}
      {searching && search.error && <div className={styles.error} role="alert">{search.error} <button onClick={load}>Retry search</button></div>}

      {selectedPartId !== null && <div className={styles.selection}>
        <span>Viewing part #{selectedPartId}</span>
        <button className={styles.refreshBtn} onClick={resetSearch}>Show all parts</button>
      </div>}

      {error && <div className={styles.error}>{error}</div>}

      {bulkIds.size > 0 && <div className={styles.selection}>
        <span>{bulkIds.size} selected{[...bulkIds].some(id => !displayed.some(part => part.id === id)) ? ' (including hidden rows)' : ''}</span>
        <button className={styles.refreshBtn} disabled={savingEdit || adjustingQuantity.size > 0 || accepting.size > 0 || deleting.size > 0 || refreshing.size > 0} onClick={() => { cancelEdit(); setBulkEditing(true) }}>Edit selected</button>
        <button className={styles.refreshBtn} onClick={() => setBulkIds(new Set())}>Clear selection</button>
      </div>}
      {bulkEditing && <BulkEditor parts={[...new Map([...parts, ...(search.page?.candidates ?? [])].map(part => [part.id, part])).values()].filter(part => part.id != null && bulkIds.has(part.id))} onClose={() => setBulkEditing(false)} onSaved={updated => {
        const changed = new Map(updated.map(part => [part.id!, part]))
        setParts(prev => prev.map(part => changed.get(part.id!) ?? part))
        remember(updated)
        setPending(prev => new Map([...prev].filter(([id]) => !changed.has(id))))
        setRowStatus(prev => { const next = new Map(prev); for (const part of updated) next.set(part.id!, 'Saved bulk edit.'); return next })
        setBulkIds(prev => new Set([...prev].filter(id => !changed.has(id))))
      }} />}

      {!loading && displayed.length === 0 && !error && !(searching && (search.pending || search.error)) && (
        <div className={styles.empty}>{selectedPartId !== null ? 'This part is no longer in inventory.' : 'No parts found.'}</div>
      )}

      {displayed.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}><input type="checkbox" aria-label="Select visible parts" checked={displayed.length > 0 && displayed.every(part => part.id != null && bulkIds.has(part.id))} disabled={displayed.length > 100 || displayed.some(part => part.id == null) || new Set([...bulkIds, ...displayed.map(part => part.id!)]).size > 100} onChange={event => { if (event.target.checked && displayed.some(part => !parts.some(known => known.id === part.id))) setParts(prev => [...prev, ...displayed.filter(part => !prev.some(known => known.id === part.id))]); setBulkIds(prev => {
                  const next = new Set(prev)
                  for (const part of displayed) { if (event.target.checked) next.add(part.id!); else next.delete(part.id!) }
                  return next
                }) }} title="Select up to 100 parts; filter inventory to narrow the selection" /></th>
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
                const isEditing = editing === id
                return (
                  <Fragment key={p.id ?? i}>
                    <tr key={p.id ?? i} className={`${styles.row} ${review ? styles.rowPending : ''}`}>
                      <td className={styles.td}><input type="checkbox" aria-label={`Select part #${id}`} checked={bulkIds.has(id)} disabled={p.id == null || (!bulkIds.has(id) && bulkIds.size >= 100)} onChange={event => { if (event.target.checked && !parts.some(part => part.id === id)) setParts(prev => [...prev, p]); setBulkIds(prev => { const next = new Set(prev); if (event.target.checked) next.add(id); else next.delete(id); return next }) }} /></td>
                      <td className={styles.td}>{p.part_category}</td>
                      <td className={styles.td}>{p.value ?? '—'}</td>
                      <td className={styles.td}>{p.package ?? '—'}</td>
                      <td className={styles.td}><QuantityControl part={p} authoritative={searching} disabled={isEditing || deleting.has(id) || accepting.has(id)} /></td>
                      <td className={styles.td}>{p.part_number ?? '—'}{p.datasheet_url && <div><a href={p.datasheet_url} target="_blank" rel="noreferrer">Datasheet</a></div>}</td>
                      <td className={styles.td}>{p.manufacturer ?? '—'}</td>
                      <td className={styles.td}>{p.description ?? '—'}</td>
                      <td className={styles.tdAction}>
                        <div className={styles.actionGroup}>
                          {!isEditing && (
                            <>
                              <button
                                className={styles.rowIconBtn}
                                disabled={adjustingQuantity.has(id) || deleting.has(id) || accepting.has(id) || savingEdit}
                                onClick={() => beginEdit(p)}
                                title="Edit part"
                                aria-label="Edit part"
                              >
                                ✎
                              </button>
                              <button
                                className={`${styles.rowIconBtn} ${styles.rowDeleteBtn}`}
                                disabled={deleting.has(id) || adjustingQuantity.has(id)}
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
                              <button className={styles.rowIconBtn} onClick={() => void showElectrical(id)} title="Show electrical specifications" aria-label="Show electrical specifications">⚡</button>
                              {(p.part_number || p.datasheet_url) && p.id != null && (
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
                    {isEditing && <tr><td colSpan={9} className={styles.td}>
                      <PartEditor key={id} part={p} saving={savingEdit} onSave={draft => void saveEdit(id, draft)} onCancel={cancelEdit} />
                    </td></tr>}
                    {expandedElectrical.has(id) && electrical.get(id) && !isEditing && <tr><td colSpan={9} className={styles.td}>
                      <SpecificationResult value={electrical.get(id)!} />
                      {electrical.get(id)?.pending_review && <div className={styles.actionGroup}>
                        <button disabled={accepting.has(id)} onClick={() => void decideElectrical(id, true)}>Accept electrical specs</button>
                        <button disabled={accepting.has(id)} onClick={() => void decideElectrical(id, false)}>Dismiss electrical review</button>
                      </div>}
                    </td></tr>}
                    {review && p.id != null && !isEditing && (
                      <FieldReviewEditor
                        key={`review-${id}`}
                        part={p}
                        review={review}
                        colSpan={9}
                        saving={accepting.has(id)}
                        onToggleField={key => toggleField(id, key)}
                        onEditField={(key, value) => editField(id, key, value)}
                        onSave={() => saveReview(id)}
                        onDismiss={() => dismissReview(id)}
                      />
                    )}
                    {!review && status && p.id != null && !isEditing && (
                      <tr key={`status-${id}`} className={styles.statusRow}>
                        <td colSpan={9} className={styles.statusCell}>
                          {status}
                        </td>
                      </tr>
                    )}
                    {showProvenance && p.id != null && !isEditing && (
                      <tr key={`provenance-${id}`} className={styles.provenanceRow}>
                        <td colSpan={9} className={styles.provenanceCell}>
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
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      </>}
    </div>
  )
}
