import { createContext, useContext } from 'react'
import type { Part } from './types'

export interface QuantityState {
  quantities: Map<number, number>
  busy: Set<number>
  errors: Map<number, string>
  remember: (parts: Part[]) => void
  adjust: (id: number, delta: -1 | 1) => Promise<void>
}

export const QuantityContext = createContext<QuantityState | null>(null)

export function useQuantities() {
  const state = useContext(QuantityContext)
  if (!state) throw new Error('Quantity controls require QuantityProvider')
  return state
}
