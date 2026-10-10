import { expect, test } from '@playwright/test'

const part = { id: 42, part_category: 'operational amplifier', profile: 'discrete_ic', quantity: 4, value: null, package: 'SOIC-8', part_number: 'LM358BIDR', manufacturer: 'Texas Instruments', description: null, datasheet_url: 'https://www.ti.com/lit/ds/symlink/lm358.pdf' }
const fact = { name: 'minimum_supply_voltage', value: '3 V', basis: 'operating_minimum', conditions: { supply_convention: 'total rail-to-rail', ambient_temperature: '-40 to 85 °C' }, evidence: { kind: 'source', url: part.datasheet_url, page: 5, excerpt: 'Reviewed supply table' } }
const review = { snapshot: { metadata: part, facts: [] }, facts: [fact] }

test('dark-mode extraction assessment keeps diagnostics collapsed and PDF links readable', async ({ context, page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await context.route('**/inventory**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/refresh')) return route.fulfill({ json: {
      part, proposed_updates: {}, provenance: [], outcome: 'source_refresh', electrical: {
        part_id: 42, facts: [], pending_review: null, clarification: 'No readable electrical ratings were found in this PDF.',
        extraction_assessment: { confidence_score: 0, score_explanation: 'Coverage measures fields, not correctness.',
          missing_fields: ['minimum_supply_voltage', 'maximum_supply_voltage', 'gain_bandwidth_product'],
          reasons: ['Some source context was omitted.'],
          relevant_pages: [{ page: 5, reason: 'Ratings or electrical context', url: `${part.datasheet_url}#page=5` }] }
      }
    } })
    return route.fulfill({ json: path === '/inventory/pending' ? { reviews: {} } : [part] })
  })
  await page.goto('/#/inventory')
  await page.getByRole('button', { name: 'Fetch specs', exact: true }).click()
  const assessment = page.getByRole('region', { name: 'PDF extraction assessment' })
  await expect(assessment).toContainText('Not extracted')
  await expect(assessment.getByText('minimum supply voltage', { exact: true })).toBeVisible()
  await expect(assessment.getByText('Some source context was omitted.')).not.toBeVisible()
  await assessment.getByText('Open datasheet pages', { exact: true }).click()
  const link = assessment.getByRole('link', { name: 'Page 5', exact: true })
  await expect(link).toBeVisible()
  const contrast = await link.evaluate(element => {
    const channels = (color: string) => (color.match(/\d+/g) ?? []).slice(0, 3).map(Number)
    const luminance = (color: string) => channels(color).map(channel => {
      const c = channel / 255
      return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4
    }).reduce((total, c, i) => total + c * [.2126, .7152, .0722][i], 0)
    const text = luminance(getComputedStyle(element).color)
    const background = luminance(getComputedStyle(element.closest('section')!).backgroundColor)
    return (Math.max(text, background) + .05) / (Math.min(text, background) + .05)
  })
  expect(contrast).toBeGreaterThanOrEqual(4.5)
})

test('inventory refresh shows electrical facts and evidence, restores review after reload, and explicitly approves', async ({ context, page }) => {
  let state = { part_id: 42, facts: [] as typeof fact[], pending_review: null as typeof review | null }
  const decisions: unknown[] = []
  await context.route('**/inventory**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/refresh')) {
      state = { ...state, pending_review: review }
      return route.fulfill({ json: { part, proposed_updates: {}, provenance: [], outcome: 'source_refresh', electrical: state } })
    }
    if (path.endsWith('/specifications/decide')) {
      const body = route.request().postDataJSON()
      decisions.push(body)
      state = { ...state, pending_review: null, facts: [fact] }
      return route.fulfill({ json: state })
    }
    if (path.endsWith('/specifications')) return route.fulfill({ json: state })
    return route.fulfill({ json: path === '/inventory/pending' ? { reviews: {} } : [part] })
  })
  await page.goto('/#/inventory')
  await expect(page.getByRole('link', { name: 'Datasheet', exact: true })).toHaveAttribute('href', part.datasheet_url)
  await page.getByRole('button', { name: 'Fetch specs', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Specification review for part 42' })).toContainText('minimum supply voltage')
  await expect(page.getByRole('link', { name: 'Source document · page 5' })).toBeVisible()
  expect(decisions).toEqual([])
  await page.reload()
  await page.getByRole('button', { name: 'Show electrical specifications', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Specification review for part 42' })).toBeVisible()
  await page.getByRole('button', { name: 'Accept electrical specs', exact: true }).click()
  await expect(page.getByText('Accepted electrical facts · Part #42', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Accept electrical specs', exact: true })).toHaveCount(0)
  expect(decisions).toEqual([{ review, approved: true }])
  await expect(page.getByText('Qty: 4', { exact: true })).toBeVisible()
})

test('every category has an editable datasheet link without changing quantity', async ({ context, page }) => {
  let stock = { ...part, part_category: 'connector', part_number: null, datasheet_url: null as string | null }
  await context.route('**/inventory**', route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON()
      expect(body.part.quantity).toBe(4)
      stock = { ...stock, ...body.part }
      return route.fulfill({ json: { part: stock } })
    }
    return route.fulfill({ json: path === '/inventory/pending' ? { reviews: {} } : [stock] })
  })
  await page.goto('/#/inventory')
  await page.getByRole('button', { name: 'Edit part', exact: true }).click()
  await page.getByLabel('Datasheet link', { exact: true }).fill('https://example.com/connector.pdf')
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Datasheet', exact: true })).toHaveAttribute('href', 'https://example.com/connector.pdf')
  await page.getByRole('button', { name: 'Edit part', exact: true }).click()
  await page.getByLabel('Datasheet link', { exact: true }).fill('')
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Datasheet', exact: true })).toHaveCount(0)
})
