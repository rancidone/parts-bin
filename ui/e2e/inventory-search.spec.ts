import { expect, test } from '@playwright/test'
import type { BrowserContext } from '@playwright/test'

const mosfets = Array.from({ length: 53 }, (_, i) => ({ id: i + 1, part_category: 'mosfet', profile: 'discrete_ic', value: null,
  package: 'SOT23', part_number: `PBSS5350T,${i + 100}`, manufacturer: 'Nexperia', description: 'PNP transistor', quantity: i === 0 ? 1 : 0 }))
const resistor = { id: 90, part_category: 'resistor', profile: 'passive', value: '10k', package: '0402', part_number: null, manufacturer: null, description: 'Drawer B', quantity: 7 }
async function fixture(context: BrowserContext) {
  const parts = [...mosfets.map(part => ({ ...part })), { ...resistor }]
  const requests: { path: string; method: string; query: URLSearchParams }[] = []
  let fail = false
  let delay = false
  let release: (() => void) | undefined
  await context.route('**/agent/**', route => route.fulfill({ json: { threads: [] } }))
  await context.route('**/inventory**', async route => {
    const url = new URL(route.request().url())
    requests.push({ path: url.pathname, method: route.request().method(), query: url.searchParams })
    if (url.pathname === '/inventory/pending') return route.fulfill({ json: { reviews: {} } })
    if (url.pathname.endsWith('/quantity')) {
      const id = Number(url.pathname.split('/')[2])
      const part = parts.find(part => part.id === id)!
      part.quantity += route.request().postDataJSON().delta
      return route.fulfill({ json: { part } })
    }
    if (url.pathname !== '/inventory/search') return route.fulfill({ json: parts })
    if (fail) return route.fulfill({ status: 503, json: { detail: 'Search temporarily unavailable' } })
    const q = url.searchParams.get('query') ?? ''
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean)
    const category = url.searchParams.get('category')
    const minimum = Number(url.searchParams.get('minimum_quantity'))
    const offset = Number(url.searchParams.get('offset'))
    const candidates = parts.filter(part => (!category || part.part_category === category) && part.quantity >= minimum
      && terms.every(term => [part.part_category, part.value, part.package, part.part_number, part.manufacturer, part.description].some(value => value?.toLowerCase().includes(term))))
    if (delay && q === 'old') await new Promise<void>(resolve => { release = resolve })
    return route.fulfill({ json: { candidates: candidates.slice(offset, offset + 50), count: candidates.length,
      next_offset: offset + 50 < candidates.length ? offset + 50 : null, match_kind: q ? 'candidate' : 'inventory' } })
  })
  return { requests, fail: (value: boolean) => { fail = value }, delay: () => { delay = true }, release: () => release?.() }
}

test('direct candidate terms, category and per-record stock use read-only API', async ({ context, page }) => {
  const f = await fixture(context)
  await page.goto('/#/inventory')
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('Nexperia 5350 SOT23')
  await expect(page.getByRole('status')).toContainText('53 candidate records')
  await page.getByLabel('Minimum stock', { exact: true }).fill('1')
  await expect(page.getByRole('status')).toContainText('1 candidate record')
  await expect(page.getByRole('cell', { name: 'PBSS5350T,100', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'PBSS5350T,101', exact: true })).toHaveCount(0)
  await page.getByLabel('Search category').selectOption('resistor')
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await page.getByLabel('Search category').selectOption('resistor')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await expect(page.getByRole('cell', { name: '10k', exact: true })).toBeVisible()
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
})

test('paging resets with filters and export contains only visible results', async ({ context, page }) => {
  await fixture(context)
  await page.goto('/#/inventory')
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('PBSS')
  await expect(page.getByRole('status')).toContainText('showing 1–50')
  await page.getByRole('button', { name: 'Next results', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  await expect(page.getByRole('cell', { name: 'PBSS5350T,150', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'PBSS5350T,100', exact: true })).toHaveCount(0)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV', exact: true }).click()
  const stream = await (await download).createReadStream()
  let csv = ''
  for await (const chunk of stream!) csv += chunk.toString()
  expect(csv).toContain('PBSS5350T,150')
  expect(csv).not.toContain('PBSS5350T,100')
  await page.getByLabel('Minimum stock', { exact: true }).fill('1')
  await expect(page.getByRole('status')).toContainText('showing 1–1')
})

test('search failure is distinct from no matches and retry recovers', async ({ context, page }) => {
  const f = await fixture(context)
  await page.goto('/#/inventory')
  f.fail(true)
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('Nexperia')
  await expect(page.getByRole('alert')).toContainText('Search temporarily unavailable')
  await expect(page.getByText('No parts found.', { exact: true })).toHaveCount(0)
  f.fail(false)
  await page.getByRole('button', { name: 'Retry search', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('53 candidate records')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('late search response cannot overwrite newer terms and direct links bypass filters', async ({ context, page }) => {
  const f = await fixture(context)
  f.delay()
  await page.goto('/#/inventory')
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('old')
  await expect.poll(() => f.requests.some(request => request.query.get('query') === 'old')).toBe(true)
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('Nexperia')
  await expect(page.getByRole('status')).toContainText('53 candidate records')
  f.release()
  await expect(page.getByRole('cell', { name: 'PBSS5350T,100', exact: true })).toBeVisible()
  await page.evaluate(() => { location.hash = '/inventory/90' })
  await expect(page.getByText('Viewing part #90', { exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '10k', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Show all parts' }).click()
  await expect(page.getByRole('textbox', { name: 'Filter inventory' })).toHaveValue('')
})

test('stock adjustment refreshes domain-filtered membership', async ({ context, page }) => {
  const f = await fixture(context)
  await page.goto('/#/inventory')
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('PBSS5350T,100')
  await page.getByLabel('Minimum stock', { exact: true }).fill('1')
  await expect(page.getByRole('status')).toContainText('1 candidate record')
  await page.getByRole('button', { name: 'Decrease quantity for PBSS5350T,100 (#1)', exact: true }).click()
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  expect(f.requests.filter(request => request.method !== 'GET').map(request => request.path)).toEqual(['/inventory/1/quantity'])
})

test('invalid minimum stock explains the problem without showing false empty results', async ({ context, page }) => {
  const f = await fixture(context)
  await page.goto('/#/inventory')
  await page.getByLabel('Minimum stock', { exact: true }).fill('1.5')
  await expect(page.getByRole('alert')).toContainText('Minimum stock must be a whole number')
  await expect(page.getByText('No parts found.', { exact: true })).toHaveCount(0)
  expect(f.requests.some(request => request.path === '/inventory/search')).toBe(false)
  await page.getByLabel('Minimum stock', { exact: true }).fill('1')
  await expect(page.getByRole('status')).toContainText('2 inventory records')
})

test('search controls and paging fit a narrow viewport', async ({ context, page }) => {
  await fixture(context)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/inventory')
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('PBSS')
  await expect(page.getByRole('status')).toContainText('showing 1–50')
  await page.getByRole('button', { name: 'Next results', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
