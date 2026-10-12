import { expect, test } from '@playwright/test'

const parts = [
  { id: 1, part_category: 'resistor', profile: 'passive', value: '10k', package: '0402', quantity: 3, part_number: null, manufacturer: null, description: 'Drawer A' as string | null },
  { id: 2, part_category: 'resistor', profile: 'passive', value: '22k', package: '0402', quantity: 7, part_number: null, manufacturer: null, description: 'Drawer B' as string | null },
]

test('bulk selection includes hidden rows and applies only checked fields after approval', async ({ context, page }) => {
  const writes: Record<string, unknown>[] = []
  let current = parts.map(part => ({ ...part }))
  await context.route('**/inventory**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/inventory/bulk/preview') {
      const body = route.request().postDataJSON()
      writes.push(body)
      expect(body).toEqual({ part_ids: [1, 2], fields: { package: '0603', description: null } })
      return route.fulfill({ json: { request_id: 'review', changes: current.map(before => ({ before, after: { ...before, ...body.fields } })), cleared_reviews: [1] } })
    }
    if (path === '/inventory/bulk/decide') {
      writes.push(route.request().postDataJSON())
      current = current.map(part => ({ ...part, package: '0603', description: null }))
      return route.fulfill({ json: { approved: true, parts: current } })
    }
    return route.fulfill({ json: path === '/inventory/pending' ? { reviews: {} } : current })
  })
  await page.goto('/#/inventory')
  await page.getByRole('checkbox', { name: 'Select visible parts' }).check()
  await page.getByPlaceholder('Filter…').fill('10k')
  await expect(page.getByText('2 selected (including hidden rows)')).toBeVisible()
  await page.getByRole('button', { name: 'Edit selected', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Review changes' })).toBeDisabled()
  await dialog.getByLabel('Change Package', { exact: true }).check()
  await dialog.getByLabel('Package', { exact: true }).fill('0603')
  await dialog.getByLabel('Change Description', { exact: true }).check()
  await dialog.getByRole('button', { name: 'Review changes' }).click()
  await expect(dialog.getByText('Saving will dismiss pending metadata reviews for 1 selected parts.')).toBeVisible()
  expect(writes).toHaveLength(1)
  await expect(dialog.getByRole('heading', { name: '#2 · 22k' })).toBeVisible()
  await dialog.getByRole('button', { name: 'Approve changes' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('cell', { name: '0603', exact: true })).toBeVisible()
  await expect(page.getByText('Qty: 3', { exact: true })).toBeVisible()
  expect(writes[1]).toEqual({ request_id: 'review', approved: true })
  await page.getByPlaceholder('Filter…').fill('')
  await expect(page.getByText('Qty: 7', { exact: true })).toBeVisible()
})

test('bulk cancel never writes and stale approval returns to editing for a fresh review', async ({ context, page }) => {
  const decisions: unknown[] = []
  let previews = 0
  await context.route('**/inventory**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/inventory/bulk/preview') {
      previews += 1
      return route.fulfill({ json: { request_id: `review-${previews}`, changes: [{ before: parts[0], after: { ...parts[0], quantity: 12 } }], cleared_reviews: [] } })
    }
    if (path === '/inventory/bulk/decide') {
      decisions.push(route.request().postDataJSON())
      return route.fulfill({ status: 409, json: { detail: { message: 'Inventory changed; request a fresh approval' } } })
    }
    return route.fulfill({ json: path === '/inventory/pending' ? { reviews: {} } : parts })
  })
  await page.goto('/#/inventory')
  await page.getByRole('checkbox', { name: 'Select part #1', exact: true }).check()
  await page.getByRole('button', { name: 'Edit selected' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(previews).toBe(0)
  expect(decisions).toHaveLength(0)
  await page.getByRole('button', { name: 'Edit selected' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Change Quantity', { exact: true }).check()
  await dialog.getByLabel('Quantity', { exact: true }).fill('1.5')
  await dialog.getByRole('button', { name: 'Review changes' }).click()
  expect(previews).toBe(0)
  await dialog.getByLabel('Quantity', { exact: true }).fill('12')
  await dialog.getByRole('button', { name: 'Review changes' }).click()
  await dialog.getByRole('button', { name: 'Approve changes' }).click()
  await expect(dialog.getByRole('alert')).toContainText('Inventory changed')
  await expect(dialog.getByLabel('Quantity', { exact: true })).toHaveValue('12')
  await dialog.getByRole('button', { name: 'Review changes' }).click()
  await expect(dialog.getByRole('button', { name: 'Approve changes' })).toBeVisible()
  expect(previews).toBe(2)
  await expect(page.getByText('Qty: 3', { exact: true })).toBeVisible()
})
