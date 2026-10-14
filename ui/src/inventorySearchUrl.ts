export const INVENTORY_PAGE_SIZE = 50
export type InventorySearch = { query: string; category: string; minimumStock: string; page: number }

export function readInventorySearch(search: string): InventorySearch {
  const params = new URLSearchParams(search)
  const rawPage = params.get('page') ?? '1'
  const page = Number(rawPage)
  const validPage = /^\d+$/.test(rawPage) && Number.isSafeInteger(page) && page >= 1
    && Number.isSafeInteger((page - 1) * INVENTORY_PAGE_SIZE)
  return { query: params.get('query') ?? '', category: params.get('category') ?? '',
    minimumStock: params.get('minimum_quantity') ?? '0', page: validPage ? page : 1 }
}

export function inventorySearchPath(state: InventorySearch): string {
  const params = new URLSearchParams()
  if (state.query) params.set('query', state.query)
  if (state.category) params.set('category', state.category)
  if (state.minimumStock !== '0') params.set('minimum_quantity', state.minimumStock)
  if (state.page !== 1) params.set('page', String(state.page))
  return `/inventory${params.size ? `?${params}` : ''}`
}
