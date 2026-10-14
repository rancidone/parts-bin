import { useRef } from 'react'
import { useLocation, useNavigate, useNavigationType, parsePath } from 'react-router'
import { inventorySearchPath, readInventorySearch, type InventorySearch } from './inventorySearchUrl'

export function useInventorySearchUrl() {
  const location = useLocation()
  const navigate = useNavigate()
  const navigationType = useNavigationType()
  const state = readInventorySearch(location.pathname === '/inventory' ? location.search : '')
  const typing = useRef<{ field: string; search: string } | null>(null)
  function update(change: Partial<InventorySearch>, field?: 'query' | 'stock' | 'value' | 'package') {
    // Hash history changes before React finishes its navigation render. Merge
    // against that current URL so rapid consecutive controls cannot resurrect filters.
    const currentRoute = parsePath(window.location.hash.slice(1))
    const currentSearch = currentRoute.pathname === '/inventory' ? currentRoute.search ?? '' : ''
    const current = readInventorySearch(currentSearch)
    const path = inventorySearchPath({ ...current, ...change })
    if (path === (currentRoute.pathname ?? '/') + (currentRoute.search ?? '')) {
      if (!field) typing.current = null
      return
    }
    const replace = Boolean(field && typing.current?.field === field
      && typing.current.search === currentSearch && navigationType !== 'POP')
    typing.current = field ? { field, search: path.includes('?') ? path.slice(path.indexOf('?')) : '' } : null
    void navigate(path, { replace })
  }
  return { ...state, update, finishTyping: () => { typing.current = null } }
}
