import { useEffect, useMemo, useState } from 'react'
import { INVENTORY_PAGE_SIZE } from './inventorySearchUrl'
import type { Part } from './types'

type Page = { candidates: Part[]; count: number; next_offset: number | null; match_kind: string }
export function useInventorySearch(enabled: boolean, query: string, category: string, minimumStock: string,
  offset: number, parts: Part[], quantities: Map<number, number>) {
  const request = useMemo(() => {
    const params = new URLSearchParams({ query: query.trim(), minimum_quantity: minimumStock, limit: String(INVENTORY_PAGE_SIZE), offset: String(offset) })
    if (category) params.set('category', category)
    return { enabled, url: `/inventory/search?${params}`, inventory: parts, stock: quantities }
  }, [enabled, query, category, minimumStock, offset, parts, quantities])
  const [result, setResult] = useState<{ request: typeof request; page: Page | null; error: string | null }>()
  useEffect(() => {
    if (!request.enabled) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void (async () => {
        try {
          if (query.length > 200) throw new Error('Search terms must contain at most 200 characters.')
          if (!/^\d+$/.test(minimumStock) || !Number.isSafeInteger(Number(minimumStock))) throw new Error('Minimum stock must be a whole number of zero or more.')
          const response = await fetch(request.url, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) })
          const data = await response.json()
          if (!response.ok) throw new Error(data.detail?.message ?? (typeof data.detail === 'string' ? data.detail : 'Could not search inventory.'))
          if (!Array.isArray(data.candidates) || !Number.isSafeInteger(data.count) || data.count < 0
            || !(data.next_offset === null || Number.isSafeInteger(data.next_offset) && data.next_offset > offset)) throw new Error('Invalid inventory search response.')
          if (!controller.signal.aborted) setResult({ request, page: data, error: null })
        } catch (error) {
          if (!controller.signal.aborted) setResult({ request, page: null, error: error instanceof Error ? error.message : 'Could not search inventory.' })
        }
      })()
    }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [request, offset, minimumStock, query])
  const current = result?.request === request ? result : undefined
  return { page: current?.page ?? null, error: current?.error ?? null, pending: enabled && !current }
}
