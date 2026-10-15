import { expect, test } from '@playwright/test'
import type { BrowserContext } from '@playwright/test'
import type { Part } from '../src/types'

const mosfets = Array.from({ length: 53 }, (_, i) => ({ id: i + 1, part_category: 'mosfet', profile: 'discrete_ic', value: null,
  package: 'SOT23', part_number: `PBSS5350T,${i + 100}`, manufacturer: 'Nexperia', description: 'PNP transistor', quantity: i === 0 ? 1 : 0 }))
const resistor = { id: 90, part_category: 'resistor', profile: 'passive', value: '10k', package: '0402', part_number: null, manufacturer: null, description: 'Drawer B', quantity: 7 }
async function fixture(context: BrowserContext, extraParts: Part[] = [], reviews: Record<string, unknown> = {}) {
  const parts: Part[] = [...mosfets.map(part => ({ ...part })), { ...resistor }, ...extraParts]
  const requests: { path: string; method: string; query: URLSearchParams }[] = []
  let fail = false
  let delay = false
  let release: (() => void) | undefined
  await context.route('**/agent/**', route => route.fulfill({ json: { threads: [] } }))
  await context.route(/\/inventory(?:\/|\?|$)/, async route => {
    const url = new URL(route.request().url())
    requests.push({ path: url.pathname, method: route.request().method(), query: url.searchParams })
    if (url.pathname === '/inventory/pending') return route.fulfill({ json: { reviews } })
    if (url.pathname.endsWith('/quantity')) {
      const id = Number(url.pathname.split('/')[2])
      const part = parts.find(part => part.id === id)!
      part.quantity += route.request().postDataJSON().delta
      return route.fulfill({ json: { part } })
    }
    if (url.pathname === '/inventory/search/suggestions') {
      const category = url.searchParams.get('category')
      const minimum = Number(url.searchParams.get('minimum_quantity'))
      const value = url.searchParams.get('value')
      const packageFilter = url.searchParams.get('package')
      const terms = (url.searchParams.get('query') ?? '').toLowerCase().split(/\s+/).filter(Boolean)
      const textAndStock = parts.filter(part => part.quantity >= minimum
        && terms.every(term => [part.description, part.manufacturer, part.part_number, part.part_category, part.value, part.package].some(field => field?.toLowerCase().includes(term))))
      const eligible = textAndStock.filter(part => !category || part.part_category === category)
      const categories = [...new Set(textAndStock.filter(part => (!packageFilter || part.package === packageFilter)
        && (!value || part.value === value || part.part_category === 'resistor' && part.value === '10k' && value === '10000 Ω')).map(part => part.part_category))].sort()
      const values = [...new Set(eligible.filter(part => !packageFilter || part.package === packageFilter).map(part => part.value).filter(value => value?.trim()))].sort()
      const packages = [...new Set(eligible.filter(part => !value || part.value === value || part.part_category === 'resistor' && part.value === '10k' && value === '10000 Ω').map(part => part.package).filter(value => value?.trim()))].sort()
      return route.fulfill({ json: { values, packages, categories } })
    }
    if (url.pathname !== '/inventory/search') return route.fulfill({ json: parts })
    if (fail) return route.fulfill({ status: 503, json: { detail: 'Search temporarily unavailable' } })
    const q = url.searchParams.get('query') ?? ''
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean)
    const category = url.searchParams.get('category')
    const minimum = Number(url.searchParams.get('minimum_quantity'))
    const offset = Number(url.searchParams.get('offset'))
    const nominal = url.searchParams.get('value')
    const packageFilter = url.searchParams.get('package')
    const candidates = parts.filter(part => (!category || part.part_category === category) && part.quantity >= minimum
      && (!nominal || part.value === nominal || part.part_category === 'resistor' && part.value === '10k' && ['10000 Ω', '10 kΩ'].includes(nominal))
      && (!packageFilter || part.package === packageFilter)
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
  await page.evaluate(() => {
    const params = new URLSearchParams(location.hash.split('?')[1])
    params.set('category', 'resistor')
    params.delete('page')
    location.hash = `/inventory?${params}`
  })
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

test('bookmarked filters and second page survive reload and a new tab', async ({ context, page }) => {
  const f = await fixture(context)
  const params = new URLSearchParams({ query: 'Nexperia PBSS SOT23', category: 'mosfet', minimum_quantity: '0', page: '2' })
  await page.goto(`/#/inventory?${params}`)
  await expect(page.getByRole('textbox', { name: 'Filter inventory' })).toHaveValue('Nexperia PBSS SOT23')
  await expect(page.getByLabel('Search category')).toHaveValue('mosfet')
  await expect(page.getByLabel('Minimum stock', { exact: true })).toHaveValue('0')
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  const bookmark = page.url()
  await page.reload()
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  await expect(page.getByRole('cell', { name: 'PBSS5350T,150', exact: true })).toBeVisible()
  const tab = await context.newPage()
  await tab.goto(bookmark)
  await expect(tab.getByRole('status')).toContainText('showing 51–53')
  await expect(tab.getByLabel('Search category')).toHaveValue('mosfet')
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
  await tab.close()
})

test('Back and Forward restore query, category, stock and page after edits and clearing', async ({ context, page }) => {
  await fixture(context)
  await page.goto('/#/inventory?query=PBSS&category=mosfet&page=2')
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  await page.getByLabel('Minimum stock', { exact: true }).fill('1')
  await expect(page.getByRole('status')).toContainText('showing 1–1')
  await page.goBack()
  await expect(page.getByLabel('Minimum stock', { exact: true })).toHaveValue('0')
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  await page.goForward()
  await expect(page.getByLabel('Minimum stock', { exact: true })).toHaveValue('1')
  await expect(page.getByRole('status')).toContainText('showing 1–1')
  await page.evaluate(() => {
    const params = new URLSearchParams(location.hash.split('?')[1])
    params.set('category', 'resistor')
    params.delete('page')
    location.hash = `/inventory?${params}`
  })
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(page).toHaveURL(/#\/inventory$/)
  await page.goBack()
  await expect(page.getByLabel('Search category')).toHaveValue('resistor')
  await expect(page.getByRole('textbox', { name: 'Filter inventory' })).toHaveValue('PBSS')
  await expect(page.getByLabel('Minimum stock', { exact: true })).toHaveValue('1')
  await page.goBack()
  await expect(page.getByLabel('Search category')).toHaveValue('mosfet')
  await page.goBack()
  await expect(page.getByRole('status')).toContainText('showing 51–53')
})

test('typing creates one search history step, including after Back', async ({ context, page }) => {
  await fixture(context)
  await page.goto('/#/inventory?query=Nexperia')
  const input = page.getByRole('textbox', { name: 'Filter inventory' })
  await input.fill('P')
  await input.pressSequentially('BSS')
  await expect(input).toHaveValue('PBSS')
  await expect(page.getByRole('status')).toContainText('53 candidate records')
  await page.goBack()
  await expect(input).toHaveValue('Nexperia')
  await input.fill('10k')
  await expect(page.getByRole('status')).toContainText('1 candidate record')
  await page.goBack()
  await expect(input).toHaveValue('Nexperia')
  await page.goForward()
  await expect(input).toHaveValue('10k')
})

test('bookmark punctuation and minimum stock restore without altering ordering suffixes', async ({ context, page }) => {
  const f = await fixture(context)
  const params = new URLSearchParams({ query: 'PBSS5350T,100', category: 'mosfet', minimum_quantity: '1' })
  await page.goto(`/#/inventory?${params}`)
  await expect(page.getByRole('status')).toContainText('1 candidate record')
  await expect(page.getByRole('cell', { name: 'PBSS5350T,100', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('textbox', { name: 'Filter inventory' })).toHaveValue('PBSS5350T,100')
  await expect(page.getByLabel('Minimum stock', { exact: true })).toHaveValue('1')
  await expect(page.getByRole('status')).toContainText('1 candidate record')
  expect(f.requests.some(request => request.query.get('query') === 'PBSS5350T,100' && request.query.get('minimum_quantity') === '1')).toBe(true)
})

test('malformed page falls back safely and stale pages offer a first-page recovery', async ({ context, page }) => {
  const f = await fixture(context)
  await page.goto('/#/inventory?query=PBSS&page=-10')
  await expect(page.getByRole('status')).toContainText('showing 1–50')
  expect(f.requests.some(request => request.query.get('offset') === '-550')).toBe(false)
  await page.goto('/#/inventory?query=PBSS&page=9')
  await expect(page.getByText('Page 9 is no longer available for these results.')).toBeVisible()
  await expect(page.getByRole('status')).toContainText('showing 0–0')
  await page.getByRole('button', { name: 'First results', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('showing 1–50')
  await expect(page).toHaveURL(/#\/inventory\?query=PBSS$/)
})

test('returning from chat and a direct part link restores the bookmarked search through history', async ({ context, page }) => {
  await fixture(context)
  await page.goto('/#/inventory?query=PBSS&category=mosfet&page=2')
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  await page.getByRole('link', { name: 'Chat', exact: true }).click()
  await page.goBack()
  await expect(page.getByRole('status')).toContainText('showing 51–53')
  await page.evaluate(() => { location.hash = '/inventory/90' })
  await expect(page.getByText('Viewing part #90', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByLabel('Search category')).toHaveValue('mosfet')
  await expect(page.getByRole('status')).toContainText('showing 51–53')
})

test('unit-aware nominal and exact package filters restore from bookmarks and history', async ({ context, page }) => {
  const f = await fixture(context)
  const params = new URLSearchParams({ category: 'resistor', value: '10000 Ω', package: '0402', minimum_quantity: '1' })
  await page.goto(`/#/inventory?${params}`)
  await expect(page.getByLabel('Nominal value', { exact: true })).toHaveValue('10000 Ω')
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('0402')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await expect(page.getByRole('cell', { name: '10k', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Nominal value', { exact: true })).toHaveValue('10000 Ω')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await page.getByLabel('Exact package', { exact: true }).fill('040')
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('0402')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await page.goForward()
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('040')
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(page.getByLabel('Nominal value', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('')
  await expect(page).toHaveURL(/#\/inventory$/)
  expect(f.requests.some(r => r.query.get('value') === '10000 Ω' && r.query.get('package') === '0402')).toBe(true)
  expect(f.requests.every(r => r.method === 'GET')).toBe(true)
})

test('package filter combines with candidate text and resets paging without losing the nominal filter', async ({ context, page }) => {
  await fixture(context)
  await page.goto('/#/inventory?category=resistor&value=10000+%CE%A9&package=0402&page=9')
  await expect(page.getByRole('button', { name: 'First results', exact: true })).toBeVisible()
  await page.getByLabel('Nominal value', { exact: true }).fill('10 kΩ')
  await expect(page.getByRole('status')).toContainText('showing 1–1')
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('0402')
  await page.getByRole('textbox', { name: 'Filter inventory', exact: true }).fill('Drawer')
  await expect(page.getByRole('status')).toContainText('1 candidate record')
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await page.getByLabel('Exact package', { exact: true }).fill('SOT23')
  await expect(page.getByRole('status')).toContainText('53 inventory records')
  await page.getByRole('textbox', { name: 'Filter inventory', exact: true }).fill('Nexperia')
  await expect(page.getByRole('status')).toContainText('53 candidate records')
  await page.getByLabel('Exact package', { exact: true }).fill('sot23')
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
})


test('package suggestions use distinct committed spelling across the whole inventory and narrow by category', async ({ context, page }) => {
  const f = await fixture(context, [
    { ...mosfets[0], id: 100, package: 'SOT-23', quantity: 0 },
    { ...mosfets[0], id: 101, package: 'sot23', quantity: 0 },
    { ...resistor, id: 102, package: '0603' },
    { ...resistor, id: 103, package: '0402' },
    { ...resistor, id: 104, package: null },
    { ...resistor, id: 105, package: '   ' },
  ], { '90': { fields: { package: { accepted: true, value: 'PendingPackage' } }, provenance: [] } })
  await page.goto('/#/inventory?category=mosfet&page=9')
  const options = page.locator('#inventory-packages option')
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveAttribute('list', 'inventory-packages')
  await expect.poll(() => options.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['SOT-23', 'SOT23', 'sot23'])
  await expect(page.getByRole('button', { name: 'First results', exact: true })).toBeVisible()
  await page.getByLabel('Search category').selectOption('resistor')
  await expect.poll(() => options.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['0402', '0603'])
  await page.getByLabel('Search category').selectOption('')
  await expect.poll(() => options.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['0402', '0603', 'SOT-23', 'SOT23', 'sot23'])
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
})

test('suggested packages keep exact filtering, bookmarks and history without changing the package on category changes', async ({ context, page }) => {
  const f = await fixture(context, [{ ...mosfets[0], id: 100, package: 'SOT-23', quantity: 2 }])
  await page.goto('/#/inventory?category=mosfet')
  const input = page.getByLabel('Exact package', { exact: true })
  const options = page.locator('#inventory-packages option')
  await expect(options).toHaveCount(2)
  // Native datalist selection writes an option's exact value into the input.
  const suggested = await options.first().getAttribute('value')
  await input.fill(suggested!)
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await expect(page).toHaveURL(/package=SOT-23/)
  await page.reload()
  await expect(input).toHaveValue('SOT-23')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await page.evaluate(() => {
    const params = new URLSearchParams(location.hash.split('?')[1])
    params.set('category', 'resistor')
    params.delete('page')
    location.hash = `/inventory?${params}`
  })
  await expect(input).toHaveValue('SOT-23')
  await expect(options).toHaveCount(1)
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByLabel('Search category')).toHaveValue('mosfet')
  await expect(options).toHaveCount(2)
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await input.fill('SOT')
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(input).toHaveValue('')
  await expect(options).toHaveCount(3)
  expect(f.requests.some(request => request.query.get('package') === 'SOT-23')).toBe(true)
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
})


test('nominal suggestions preserve distinct committed notation and category filtering', async ({ context, page }) => {
  const f = await fixture(context, [
    { ...resistor, id: 100, value: '10k', quantity: 0 },
    { ...resistor, id: 101, value: '10 MΩ', quantity: 0 },
    { ...resistor, id: 102, value: '10 mΩ', quantity: 0 },
    { ...resistor, id: 103, value: '10000 Ω', quantity: 0 },
    { ...resistor, id: 104, value: null },
    { ...resistor, id: 105, value: '   ' },
    { ...resistor, id: 106, part_category: 'capacitor', value: '100n' },
  ], { '90': { fields: { value: { accepted: true, value: 'PendingValue' } }, provenance: [] } })
  await page.goto('/#/inventory?category=resistor&page=9')
  const options = page.locator('#inventory-nominal-values option')
  await expect(page.getByLabel('Nominal value', { exact: true })).toHaveAttribute('list', 'inventory-nominal-values')
  await expect.poll(() => options.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['10 MΩ', '10 mΩ', '10000 Ω', '10k'])
  await expect(page.getByRole('button', { name: 'First results', exact: true })).toBeVisible()
  await page.getByLabel('Search category').selectOption('capacitor')
  await expect.poll(() => options.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['100n'])
  await page.evaluate(() => {
    const params = new URLSearchParams(location.hash.split('?')[1])
    params.set('category', 'mosfet')
    params.delete('page')
    location.hash = `/inventory?${params}`
  })
  await expect(options).toHaveCount(0)
  await page.getByLabel('Search category').selectOption('')
  await expect.poll(() => options.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['10 MΩ', '10 mΩ', '10000 Ω', '100n', '10k'])
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
})

test('suggested nominal value and typed equivalents retain bookmarks, unit matching and history', async ({ context, page }) => {
  const f = await fixture(context)
  await page.goto('/#/inventory?category=resistor&package=0402&page=9')
  const input = page.getByLabel('Nominal value', { exact: true })
  const options = page.locator('#inventory-nominal-values option')
  await expect(options).toHaveCount(1)
  const suggested = await options.first().getAttribute('value')
  await input.fill(suggested!)
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await expect(page).toHaveURL(/value=10k/)
  await expect(page).not.toHaveURL(/page=9/)
  await input.blur()
  await input.fill('10000 Ω')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await input.blur()
  await page.reload()
  await expect(input).toHaveValue('10000 Ω')
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('0402')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await page.evaluate(() => {
    const params = new URLSearchParams(location.hash.split('?')[1])
    params.set('category', 'mosfet')
    params.delete('page')
    location.hash = `/inventory?${params}`
  })
  await expect(input).toHaveValue('10000 Ω')
  await expect(options).toHaveCount(0)
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByLabel('Search category')).toHaveValue('resistor')
  await expect(options).toHaveCount(1)
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await page.goForward()
  await expect(page.getByLabel('Search category')).toHaveValue('mosfet')
  await expect(input).toHaveValue('10000 Ω')
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(input).toHaveValue('')
  await expect(page).toHaveURL(/#\/inventory$/)
  expect(f.requests.some(request => request.query.get('value') === '10000 Ω')).toBe(true)
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
})


test('suggestions narrow each other with unit-aware values and per-record stock and recover through history', async ({ context, page }) => {
  await fixture(context, [
    { ...resistor, id: 100, package: '0603', quantity: 3 },
    { ...resistor, id: 101, value: '22k', package: '0402', quantity: 2 },
    { ...resistor, id: 102, value: '47k', package: '0805', quantity: 0 },
  ])
  await page.goto('/#/inventory?category=resistor&value=10000+%CE%A9&package=0402&minimum_quantity=1')
  const values = page.locator('#inventory-nominal-values option')
  const packages = page.locator('#inventory-packages option')
  const names = (nodes: Element[]) => nodes.map(node => node.getAttribute('value'))
  await expect.poll(() => values.evaluateAll(names)).toEqual(['10k', '22k'])
  await expect.poll(() => packages.evaluateAll(names)).toEqual(['0402', '0603'])
  await page.getByLabel('Minimum stock', { exact: true }).fill('4')
  await page.getByLabel('Minimum stock', { exact: true }).blur()
  await expect.poll(() => values.evaluateAll(names)).toEqual(['10k'])
  await expect.poll(() => packages.evaluateAll(names)).toEqual(['0402'])
  await page.reload()
  await expect.poll(() => packages.evaluateAll(names)).toEqual(['0402'])
  await page.getByLabel('Exact package', { exact: true }).fill('0603')
  await page.getByLabel('Exact package', { exact: true }).blur()
  await expect(values).toHaveCount(0)
  await expect.poll(() => packages.evaluateAll(names)).toEqual(['0402'])
  await expect(page.getByLabel('Nominal value', { exact: true })).toHaveValue('10000 Ω')
  await page.goBack()
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('0402')
  await expect.poll(() => values.evaluateAll(names)).toEqual(['10k'])
  await page.goForward()
  await expect(page.getByLabel('Exact package', { exact: true })).toHaveValue('0603')
  await expect(values).toHaveCount(0)
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect.poll(() => values.evaluateAll(names)).toEqual(['10k', '22k', '47k'])
})

test('suggestion failure clears options and retries without changing bookmarked filters', async ({ context, page }) => {
  await fixture(context)
  let fail = true
  await context.route('**/inventory/search/suggestions?**', async route => {
    if (fail) return route.fulfill({ status: 503, json: { detail: 'Suggestions temporarily unavailable' } })
    await route.fallback()
  })
  await page.goto('/#/inventory?category=resistor&value=10000+%CE%A9&package=0402')
  await expect(page.getByRole('alert')).toContainText('Suggestions temporarily unavailable')
  await expect(page.locator('#inventory-nominal-values option')).toHaveCount(0)
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  fail = false
  await page.getByRole('button', { name: 'Retry suggestions' }).click()
  await expect(page.locator('#inventory-nominal-values option')).toHaveAttribute('value', '10k')
  await expect(page.getByLabel('Nominal value', { exact: true })).toHaveValue('10000 Ω')
})


test('late suggestion responses cannot restore options from a previous category and stock edits refresh suggestions', async ({ context, page }) => {
  await fixture(context)
  let release: (() => void) | undefined
  let held = false
  await context.route('**/inventory/search/suggestions?**', async route => {
    if (new URL(route.request().url()).searchParams.get('category') === 'resistor') {
      held = true
      await new Promise<void>(resolve => { release = resolve })
      return route.fulfill({ json: { values: ['stale'], packages: ['stale'], categories: ['stale'] } }).catch(() => {})
    }
    await route.fallback()
  })
  await page.goto('/#/inventory?category=resistor')
  await expect.poll(() => held).toBe(true)
  await page.evaluate(() => {
    const params = new URLSearchParams(location.hash.split('?')[1])
    params.set('category', 'mosfet')
    params.delete('page')
    location.hash = `/inventory?${params}`
  })
  const packages = page.locator('#inventory-packages option')
  await expect(packages).toHaveAttribute('value', 'SOT23')
  release?.()
  await expect(packages).toHaveAttribute('value', 'SOT23')
  await page.getByLabel('Minimum stock', { exact: true }).fill('1')
  await expect(page.getByRole('status')).toContainText('1 inventory record')
  await expect(packages).toHaveAttribute('value', 'SOT23')
  await page.getByRole('button', { name: 'Decrease quantity for PBSS5350T,100 (#1)', exact: true }).click()
  await expect(page.getByText('No parts found.', { exact: true })).toBeVisible()
  await expect(packages).toHaveCount(0)
})


test('text narrows both suggestion lists across fields while unit-aware value and bookmarks survive', async ({ context, page }) => {
  const f = await fixture(context, [
    { ...resistor, id: 100, description: 'Drawer C', package: '0603', quantity: 3 },
    { ...resistor, id: 101, description: 'Drawer B', value: '22k', package: '0402', quantity: 2 },
  ])
  const params = new URLSearchParams({ category: 'resistor', query: 'Drawer B', value: '10000 Ω', minimum_quantity: '1' })
  await page.goto(`/#/inventory?${params}`)
  const values = page.locator('#inventory-nominal-values option')
  const packages = page.locator('#inventory-packages option')
  await expect.poll(() => values.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['10k', '22k'])
  await expect(packages).toHaveAttribute('value', '0402')
  await page.reload()
  await expect(page.getByRole('textbox', { name: 'Filter inventory' })).toHaveValue('Drawer B')
  await expect(packages).toHaveAttribute('value', '0402')
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('Drawer C')
  await page.getByRole('textbox', { name: 'Filter inventory' }).blur()
  await expect(packages).toHaveAttribute('value', '0603')
  await expect(values).toHaveAttribute('value', '10k')
  await page.goBack()
  await expect(packages).toHaveAttribute('value', '0402')
  await page.goForward()
  await expect(packages).toHaveAttribute('value', '0603')
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('10000r')
  await expect(values).toHaveCount(0)
  await expect(packages).toHaveCount(0)
  await expect(page.getByLabel('Nominal value', { exact: true })).toHaveValue('10000 Ω')
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect.poll(() => packages.evaluateAll(nodes => nodes.map(node => node.getAttribute('value')))).toEqual(['0402', '0603', 'SOT23'])
  expect(f.requests.some(request => request.path === '/inventory/search/suggestions' && request.query.get('query') === 'Drawer B' && request.query.get('value') === '10000 Ω')).toBe(true)
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
})

test('late text suggestion response cannot overwrite a newer query', async ({ context, page }) => {
  await fixture(context)
  let release: (() => void) | undefined
  let held = false
  await context.route('**/inventory/search/suggestions?**', async route => {
    if (new URL(route.request().url()).searchParams.get('query') === 'old') {
      held = true
      await new Promise<void>(resolve => { release = resolve })
      return route.fulfill({ json: { values: ['stale'], packages: ['stale'], categories: ['stale'] } }).catch(() => {})
    }
    await route.fallback()
  })
  await page.goto('/#/inventory?query=old')
  await expect.poll(() => held).toBe(true)
  await page.getByRole('textbox', { name: 'Filter inventory' }).fill('Drawer B')
  await expect(page.locator('#inventory-packages option')).toHaveAttribute('value', '0402')
  release?.()
  await expect(page.locator('#inventory-packages option')).toHaveAttribute('value', '0402')
})


test('category options reflect other filters and preserve a nonmatching bookmarked selection', async ({ context, page }) => {
  const f = await fixture(context, [
    { ...resistor, id: 100, part_category: 'capacitor', value: '100n', quantity: 6 },
    { ...resistor, id: 101, package: '0603', quantity: 3 },
  ])
  await page.goto('/#/inventory?category=mosfet&query=Drawer&value=10000+%CE%A9&package=0402&minimum_quantity=1')
  const category = page.getByLabel('Search category')
  const options = category.locator('option')
  await expect(category).toHaveValue('mosfet')
  await expect(category.getByRole('option', { name: 'mosfet (no matching records)', exact: true })).toBeAttached()
  await expect.poll(() => options.evaluateAll(nodes => nodes.map(node => (node as HTMLOptionElement).value))).toEqual(['', 'mosfet', 'resistor'])
  await category.selectOption('resistor')
  await expect(page.getByRole('status')).toContainText('1 candidate record')
  await page.getByLabel('Nominal value', { exact: true }).fill('')
  await expect(category.getByRole('option', { name: 'capacitor', exact: true })).toBeAttached()
  await page.getByLabel('Minimum stock', { exact: true }).fill('8')
  await page.getByLabel('Minimum stock', { exact: true }).blur()
  await expect(category.getByRole('option', { name: 'resistor (no matching records)', exact: true })).toBeAttached()
  await expect(options).toHaveCount(2)
  await page.reload()
  await expect(category).toHaveValue('resistor')
  await expect(category.getByRole('option', { name: 'resistor (no matching records)', exact: true })).toBeAttached()
  await page.goBack()
  await expect(page.getByLabel('Minimum stock', { exact: true })).toHaveValue('1')
  await expect(category.getByRole('option', { name: 'capacitor', exact: true })).toBeAttached()
  await page.goForward()
  await expect(category.getByRole('option', { name: 'resistor (no matching records)', exact: true })).toBeAttached()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(category).toHaveValue('')
  await expect(options).toHaveCount(4)
  expect(f.requests.every(request => request.method === 'GET')).toBe(true)
})

test('inventory displays the domain label while preserving decimal values for editing', async ({ context, page }) => {
  await fixture(context, [{ id: 200, part_category: 'resistor', profile: 'passive', value: '2.2',
    value_unit: 'Ω', display_value: '2R2', package: '0603', part_number: null, manufacturer: null,
    description: 'Canonical nominal quantity', quantity: 20 }])
  await page.goto('/#/inventory?category=resistor')
  await expect(page.getByRole('cell', { name: '2R2', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '2.2', exact: true })).toHaveCount(0)
})
