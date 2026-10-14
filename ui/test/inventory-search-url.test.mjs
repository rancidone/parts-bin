import assert from 'node:assert/strict'
import { test } from 'node:test'
import { inventorySearchPath, readInventorySearch } from '../src/inventorySearchUrl.ts'

test('bookmark round trip preserves punctuation, Unicode, whitespace and all filters', () => {
  const state = { query: 'PBSS,215 + 10 µF & 50% #one ', category: 'op_amp', minimumStock: '3', page: 2 }
  const path = inventorySearchPath(state)
  assert.ok(path.startsWith('/inventory?'))
  assert.deepEqual(readInventorySearch(path.slice(path.indexOf('?'))), state)
})

test('empty search has a canonical bare inventory link', () => {
  assert.equal(inventorySearchPath(readInventorySearch('')), '/inventory')
  assert.equal(inventorySearchPath(readInventorySearch('?page=1&minimum_quantity=0')), '/inventory')
})

test('invalid or unsafe bookmark pages fall back to the first page', () => {
  for (const page of ['-1', '0', '1.5', 'wat', 'Infinity', '9007199254740991', '1e3', '']) {
    assert.equal(readInventorySearch(`?query=PBSS&page=${page}`).page, 1)
  }
  assert.equal(readInventorySearch('?page=2').page, 2)
})

test('invalid stock and long query remain visible for validation rather than changing the search silently', () => {
  const query = 'x'.repeat(201)
  const state = readInventorySearch(`?query=${query}&minimum_quantity=1.5`)
  assert.equal(state.query, query)
  assert.equal(state.minimumStock, '1.5')
})
