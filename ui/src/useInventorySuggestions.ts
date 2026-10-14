import { useEffect, useMemo, useState } from 'react'
import type { Part } from './types'

type Suggestions = { values: string[]; packages: string[]; categories: string[] }
export function useInventorySuggestions(enabled: boolean, query: string, category: string, minimumStock: string,
  value: string, packageFilter: string, parts: Part[], quantities: Map<number, number>) {
  const [attempt, setAttempt] = useState(0)
  const request = useMemo(() => {
    const params = new URLSearchParams({ query: query.trim(), minimum_quantity: minimumStock })
    if (category) params.set('category', category)
    if (value.trim()) params.set('value', value.trim())
    if (packageFilter.trim()) params.set('package', packageFilter.trim())
    return { enabled, url: `/inventory/search/suggestions?${params}`, parts, quantities, attempt }
  }, [enabled, query, category, minimumStock, value, packageFilter, parts, quantities, attempt])
  const [result, setResult] = useState<{ request: typeof request; data: Suggestions | null; error: string | null }>()
  useEffect(() => {
    if (!request.enabled) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void (async () => {
        try {
          if (query.length > 200) throw new Error('Search terms must contain at most 200 characters.')
          if (value.length > 200 || packageFilter.length > 200) throw new Error('Nominal value and package must contain at most 200 characters.')
          if (!/^\d+$/.test(minimumStock) || !Number.isSafeInteger(Number(minimumStock))) throw new Error('Minimum stock must be a whole number of zero or more.')
          const response = await fetch(request.url, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) })
          const data = await response.json()
          if (!response.ok) throw new Error(data.detail?.message ?? (typeof data.detail === 'string' ? data.detail : 'Could not load suggestions.'))
          if (![data.values, data.packages, data.categories].every(list => Array.isArray(list) && list.every(item => typeof item === 'string'))) throw new Error('Invalid inventory suggestions response.')
          if (!controller.signal.aborted) setResult({ request, data, error: null })
        } catch (error) {
          if (!controller.signal.aborted) setResult({ request, data: null, error: error instanceof Error ? error.message : 'Could not load suggestions.' })
        }
      })()
    }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [request, query, value, packageFilter, minimumStock])
  const current = result?.request === request ? result : undefined
  return { categories: current?.data?.categories ?? [], values: current?.data?.values ?? [], packages: current?.data?.packages ?? [], error: current?.error,
    pending: enabled && !current, retry: () => setAttempt(number => number + 1) }
}
