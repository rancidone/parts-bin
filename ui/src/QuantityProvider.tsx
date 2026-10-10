import { useCallback, useRef, useState, type ReactNode } from 'react'
import { QuantityContext } from './quantityContext'
import type { Part } from './types'

export function QuantityProvider({ children }: { children: ReactNode }) {
  const [quantities, setQuantities] = useState(new Map<number, number>())
  const [busy, setBusy] = useState(new Set<number>())
  const [errors, setErrors] = useState(new Map<number, string>())
  const inFlight = useRef(new Set<number>())

  const remember = useCallback((parts: Part[]) => {
    setQuantities(previous => {
      const next = new Map(previous)
      for (const part of parts) if (part.id != null) next.set(part.id, part.quantity)
      return next
    })
  }, [])

  async function adjust(id: number, delta: -1 | 1) {
    if (inFlight.current.has(id)) return
    inFlight.current.add(id)
    setBusy(previous => new Set(previous).add(id))
    setErrors(previous => { const next = new Map(previous); next.delete(id); return next })
    try {
      const response = await fetch(`/inventory/${id}/quantity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delta }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail?.message ?? (typeof data.detail === 'string' ? data.detail : response.statusText))
      remember([data.part])
    } catch (error) {
      setErrors(previous => new Map(previous).set(id, `Could not update quantity: ${error instanceof Error ? error.message : String(error)}`))
    } finally {
      inFlight.current.delete(id)
      setBusy(previous => { const next = new Set(previous); next.delete(id); return next })
    }
  }

  return <QuantityContext value={{ quantities, busy, errors, remember, adjust }}>{children}</QuantityContext>
}
