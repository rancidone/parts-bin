import { expect, test } from '@playwright/test'

const initial = { id: 42, part_category: 'resistor', profile: 'passive', value: '10k', package: '0603', part_number: null, manufacturer: null, description: null, quantity: 3 }

test('inventory quantity controls preserve stock on failure and disable decrement at zero', async ({ context, page }) => {
  let part = { ...initial, quantity: 1 }
  let fail = false
  const changes: unknown[] = []
  await context.route('**/inventory**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/quantity')) {
      const body = route.request().postDataJSON()
      changes.push(body)
      if (fail) return route.fulfill({ status: 422, json: { detail: { message: 'quantity cannot be negative' } } })
      part = { ...part, quantity: part.quantity + body.delta }
      return route.fulfill({ json: { part } })
    }
    return route.fulfill({ json: path === '/inventory/pending' ? { reviews: {} } : [part] })
  })
  await page.goto('/#/inventory')
  const decrease = page.getByRole('button', { name: 'Decrease quantity for resistor 10k (#42)', exact: true })
  const increase = page.getByRole('button', { name: 'Increase quantity for resistor 10k (#42)', exact: true })
  await decrease.click()
  await expect(page.getByText('Qty: 0', { exact: true })).toBeVisible()
  await expect(decrease).toBeDisabled()
  await increase.click()
  await expect(page.getByText('Qty: 1', { exact: true })).toBeVisible()
  fail = true
  await increase.click()
  await expect(page.getByRole('alert')).toContainText('quantity cannot be negative')
  await expect(page.getByText('Qty: 1', { exact: true })).toBeVisible()
  expect(changes).toEqual([{ delta: -1 }, { delta: 1 }, { delta: 1 }])
})

test('historical cards adjust current stock and share the count with inventory', async ({ context, page }) => {
  let part = { ...initial, quantity: 1 }
  const events = [
    { sequence: 1, kind: 'tool_result', data: { name: 'search_parts', result: { ok: true, result: { parts: [{ ...initial, quantity: 9 }], count: 1 } } } },
    { sequence: 2, kind: 'completed', data: { status: 'completed' } },
  ].map(event => ({ ...event, runtime: 'openai', thread_id: 'current' }))
  await context.addInitScript(() => localStorage.setItem('parts-bin.current-thread', 'current'))
  await context.route('**/agent/**', route => {
    if (new URL(route.request().url()).pathname === '/agent/threads') return route.fulfill({ json: { threads: [{ thread_id: 'current', runtime: 'openai', title: 'current', last_sequence: 2 }] } })
    return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `event: agent_event\ndata: ${JSON.stringify(event)}\n\n`).join('') })
  })
  await context.route('**/inventory**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/quantity')) {
      expect(route.request().postDataJSON()).toEqual({ delta: -1 })
      part = { ...part, quantity: part.quantity - 1 }
      return route.fulfill({ json: { part } })
    }
    return route.fulfill({ json: path === '/inventory/pending' ? { reviews: {} } : [part] })
  })
  await page.goto('/')
  await expect(page.getByText('Qty: 9', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Decrease quantity for resistor 10k (#42)' }).click()
  await expect(page.getByText('Qty: 0', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'View in inventory', exact: true }).click()
  await expect(page.getByText('Viewing part #42')).toBeVisible()
  await expect(page.getByText('Qty: 0', { exact: true }).filter({ visible: true })).toBeVisible()
  await page.getByRole('link', { name: 'Chat', exact: true }).click()
  await expect(page.getByText('Qty: 0', { exact: true }).filter({ visible: true })).toBeVisible()
})

test('editor supports exact counts, validation, cancellation, and retry after failure', async ({ context, page }) => {
  let part = { ...initial }
  const writes: Record<string, unknown>[] = []
  let fail = true
  await context.route('**/inventory**', route => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON()
      writes.push(body)
      if (fail) return route.fulfill({ status: 503, body: 'Please retry' })
      part = { ...part, ...body.part }
      return route.fulfill({ json: { part } })
    }
    return route.fulfill({ json: new URL(route.request().url()).pathname === '/inventory/pending' ? { reviews: {} } : [part] })
  })
  await page.goto('/#/inventory')
  await page.getByRole('button', { name: 'Edit part', exact: true }).click()
  let editor = page.getByRole('form', { name: 'Edit part #42' })
  await editor.getByLabel('Quantity', { exact: true }).fill('99')
  await editor.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByText('Qty: 3', { exact: true })).toBeVisible()
  expect(writes).toHaveLength(0)
  await page.getByRole('button', { name: 'Edit part', exact: true }).click()
  editor = page.getByRole('form', { name: 'Edit part #42' })
  await editor.getByLabel('Quantity', { exact: true }).fill('1.5')
  await editor.getByRole('button', { name: 'Save changes' }).click()
  expect(writes).toHaveLength(0)
  await editor.getByLabel('Quantity', { exact: true }).fill('12')
  await editor.getByLabel('Description').fill('Drawer A')
  await editor.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Error: Please retry')).toBeVisible()
  await expect(editor.getByLabel('Quantity', { exact: true })).toHaveValue('12')
  fail = false
  await editor.getByRole('button', { name: 'Save changes' }).click()
  await expect(editor).toHaveCount(0)
  await expect(page.getByText('Qty: 12', { exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Drawer A', exact: true })).toBeVisible()
  expect(writes[1]).toMatchObject({ part: { quantity: 12, description: 'Drawer A' } })
  expect((writes[1].part as Record<string, unknown>).id).toBeUndefined()
})
