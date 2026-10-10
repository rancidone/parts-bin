import { expect, test } from '@playwright/test'
import type { BrowserContext, Page, Request, Route } from '@playwright/test'
import type { AgentEvent } from '../src/types'

const key = 'parts-bin.current-thread'
const event = (sequence: number, kind: AgentEvent['kind'], data: Record<string, unknown>, thread = 'current', runtime = 'openai'): AgentEvent =>
  ({ sequence, kind, data, thread_id: thread, runtime })
const user = event(1, 'user_message', { text: 'Add a resistor', execution_id: 'work' })
const done = event(3, 'completed', { status: 'completed', execution_id: 'work' })
const proposal = event(2, 'approval_request', { execution_id: 'work', request_id: 'review', effect: 'Delete resistor', tool: 'delete_part' })
const waiting = event(3, 'completed', { status: 'awaiting_approval', execution_id: 'work' })
const photo = { name: 'fresh.png', mimeType: 'image/png', buffer: Buffer.from('synthetic image bytes') }
const stream = (route: Route, events: AgentEvent[]) => route.fulfill({
  contentType: 'text/event-stream',
  body: events.map(item => `event: agent_event\ndata: ${JSON.stringify(item)}\n\n`).join(''),
})

async function chooseConversation(page: Page, title: string) {
  await page.getByRole('button', { name: 'Conversation', exact: true }).click()
  await page.getByRole('region', { name: 'Conversation history' }).getByRole('button', { name: new RegExp(`^${title} `) }).click()
}

async function fixture(context: BrowserContext, initial: AgentEvent[]) {
  const histories = new Map<string, AgentEvent[]>([
    ['current', initial],
    ['other', [event(1, 'assistant_text', { text: 'Other conversation' }, 'other')]],
    ['retired', [event(1, 'assistant_text', { text: 'Historical provider message' }, 'retired', 'codex')]],
  ])
  const posts: Request[] = []
  let write: (route: Route) => Promise<void> = route => stream(route, [user, done])
  let historyUnavailable = false
  await context.route('**/agent/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST') {
      posts.push(request)
      return write(route)
    }
    if (path === '/agent/threads') return route.fulfill({ json: { threads: [...histories].map(([thread_id, events]) => ({
      thread_id, runtime: events[0]?.runtime ?? 'openai', title: thread_id, last_sequence: events.at(-1)?.sequence ?? 0,
    })) } })
    if (historyUnavailable) return route.fulfill({ status: 503, json: { detail: 'History temporarily unavailable' } })
    const thread = path.split('/')[3]
    if (!histories.has(thread)) throw new Error(`Unexpected history request: ${path}`)
    return stream(route, histories.get(thread)!)
  })
  const open = async (page: Page) => {
    await page.goto('/')
    // Set the browser pointer once; reloads must use the application's stored value.
    await page.evaluate(key => localStorage.setItem(key, 'current'), key)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Conversation', exact: true })).toHaveText('current▾')
    await expect(page.getByRole('button', { name: 'Refresh history' })).toBeEnabled()
  }
  return { histories, posts, open, respond: (handler: typeof write) => { write = handler },
    failHistory: (failed: boolean) => { historyUnavailable = failed } }
}

test('assistant Markdown tables render in streamed replies and restored history', async ({ context, page }) => {
  const text = `You have four op-amp records in your inventory:

| Part | Package | Quantity | Manufacturer |
|---|---|---|---|
| UA741CP | SOIC | 11 | Texas Instruments |
| LM358 | Not recorded | 1 | Not recorded |
| UA741CN | DIP | 6 | STMicroelectronics |
| HA17458 | DIP | 1 | Not recorded |

**Verified** with [source](https://example.com).

<script>window.markdownExecuted = true</script>
[unsafe](javascript:alert(1))`
  const reply = event(2, 'assistant_text', { text, execution_id: 'work' })
  const f = await fixture(context, [])
  await page.setViewportSize({ width: 390, height: 844 })
  await f.open(page)
  f.respond(route => { f.histories.set('current', [user, reply, done]); return stream(route, [user, reply, done]) })
  await page.getByRole('textbox', { name: 'Message' }).fill('Show op-amps')
  await page.getByRole('button', { name: 'Send message' }).click()
  const check = async () => {
    const table = page.getByRole('table')
    await expect(table).toBeVisible()
    await expect(table.getByRole('columnheader')).toHaveText(['Part', 'Package', 'Quantity', 'Manufacturer'])
    await expect(table.getByRole('row')).toHaveCount(5)
    await expect(table.getByRole('cell', { name: 'UA741CP', exact: true })).toBeVisible()
    await expect(page.locator('strong').filter({ hasText: 'Verified' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'source', exact: true })).toHaveAttribute('href', 'https://example.com')
    await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0)
    expect(await page.evaluate(() => 'markdownExecuted' in window)).toBe(false)
    const region = page.getByRole('region', { name: 'Response table' })
    expect(await region.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  }
  await check()
  await page.reload()
  await check()
})

for (const restored of [false, true]) {
  test(`part links open the exact inventory record from ${restored ? 'restored history' : 'a streamed reply'}`, async ({ context, page }) => {
    const part = { id: 42, part_category: 'operational amplifier', profile: 'discrete_ic', part_number: 'UA741CP', package: 'SOIC', quantity: 11, manufacturer: 'Texas Instruments', value: null, description: null }
    const other = { ...part, id: 43, package: 'DIP', quantity: 6 }
    const result = event(2, 'tool_result', { name: 'search_parts', result: { ok: true, result: { parts: [part], count: 1 } } })
    const reply = event(3, 'assistant_text', { text: '| Part | Package |\n|---|---|\n| `UA741CP` | SOIC |\n| UNKNOWN | DIP |' })
    const completed = event(4, 'completed', { status: 'completed', execution_id: 'work' })
    const history = [user, result, reply, completed]
    const f = await fixture(context, restored ? history : [])
    const writes: string[] = []
    await context.route('**/inventory**', route => {
      if (route.request().method() !== 'GET') writes.push(route.request().method())
      return route.fulfill({ json: new URL(route.request().url()).pathname === '/inventory/pending' ? { reviews: {} } : [part, other] })
    })
    await f.open(page)
    if (!restored) {
      f.respond(route => stream(route, history))
      await page.getByRole('textbox', { name: 'Message' }).fill('Show op-amps')
      await page.getByRole('button', { name: 'Send message' }).click()
    }
    const link = page.getByRole('button', { name: 'View UA741CP in inventory' })
    await expect(link).toBeVisible()
    await expect(page.getByRole('button', { name: 'View UNKNOWN in inventory' })).toHaveCount(0)
    await link.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText('Viewing part #42')).toBeVisible()
    await expect(page.getByRole('cell', { name: 'SOIC', exact: true })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'DIP', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Show all parts' }).click()
    await expect(page.getByRole('cell', { name: 'DIP', exact: true })).toBeVisible()
    await page.getByRole('link', { name: 'Chat', exact: true }).click()
    await expect(link).toBeVisible()
    await page.getByRole('button', { name: 'View in inventory', exact: true }).click()
    await expect(page.getByText('Viewing part #42')).toBeVisible()
    expect(writes).toEqual([])
  })
}

test('ambiguous part numbers are not linked and deleted records show a clear message', async ({ context, page }) => {
  const first = { id: 1, part_number: 'LM358', part_category: 'op-amp', quantity: 1 }
  const second = { ...first, id: 2 }
  const unique = { ...first, id: 3, part_number: 'UA741CN' }
  const result = event(2, 'tool_result', { name: 'search_parts', result: { ok: true, result: { parts: [first, second, unique] } } })
  const reply = event(3, 'assistant_text', { text: '| Part |\n|---|\n| LM358 |\n| UA741CN |' })
  const f = await fixture(context, [user, result, reply, done])
  await context.route('**/inventory**', route => route.fulfill({ json: new URL(route.request().url()).pathname === '/inventory/pending' ? { reviews: {} } : [] }))
  await f.open(page)
  await expect(page.getByRole('button', { name: 'View LM358 in inventory' })).toHaveCount(0)
  await page.getByRole('button', { name: 'View part #2 in inventory', exact: true }).click()
  await expect(page.getByText('Viewing part #2')).toBeVisible()
  await page.getByRole('link', { name: 'Chat', exact: true }).click()
  await page.getByRole('button', { name: 'View UA741CN in inventory' }).click()
  await expect(page.getByText('This part is no longer in inventory.')).toBeVisible()
})

test('interrupted response requires read-only reconnect before explicit resume', async ({ context, page }) => {
  const f = await fixture(context, [])
  await f.open(page)
  f.respond(route => { f.histories.set('current', [user]); return stream(route, [user]) })
  await page.getByRole('textbox', { name: 'Message' }).fill('Add a resistor')
  await page.getByRole('button', { name: 'Send message' }).click()
  await expect(page.getByRole('alert')).toContainText('interrupted')
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('Add a resistor')
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Resume request' })).toBeDisabled()
  await page.getByRole('button', { name: 'Refresh history' }).click()
  await expect(page.getByRole('status')).toHaveText('Unfinished work')
  await page.reload()
  await expect(page.getByRole('status')).toHaveText('Unfinished work')
  expect(f.posts).toHaveLength(1)
  f.respond(route => { f.histories.set('current', [user, done]); return stream(route, [user, done, done]) })
  await page.getByRole('button', { name: 'Resume request' }).click()
  await expect(page.getByRole('status')).toHaveText('Ready')
  await expect(page.getByText('Add a resistor', { exact: true }).and(page.locator('div'))).toHaveCount(1)
  expect(f.posts).toHaveLength(2)
  expect(new URL(f.posts[1].url()).pathname).toBe('/agent/threads/current/resume')
  expect(f.posts[1].postData()).toContain('name="execution_id"\r\n\r\nwork')
  expect(f.posts[1].postData()).not.toContain('name="message"')
})

test('unavailable history blocks writes until refresh succeeds and retains the draft', async ({ context, page }) => {
  const f = await fixture(context, [user, done])
  await f.open(page)
  await page.getByRole('textbox', { name: 'Message' }).fill('Keep this draft')
  f.failHistory(true)
  await page.getByRole('button', { name: 'Refresh history' }).click()
  await expect(page.getByRole('alert')).toHaveText('History temporarily unavailable')
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeDisabled()
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('Keep this draft')
  f.failHistory(false)
  await page.getByRole('button', { name: 'Refresh history' }).click()
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeEnabled()
  expect(f.posts).toHaveLength(0)
})

test('interrupted photo needs a fresh upload; reload retains no image', async ({ context, page }) => {
  const photoUser = event(1, 'user_message', { execution_id: 'work', image: { media_type: 'image/png' } })
  const f = await fixture(context, [photoUser])
  await f.open(page)
  await expect(page.getByRole('button', { name: 'Resume request' })).toBeDisabled()
  await page.locator('input[type=file]').setInputFiles(photo)
  await expect(page.getByRole('img', { name: 'attachment' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Resume request' })).toBeDisabled()
  await expect(page.getByRole('img', { name: 'attachment' })).toHaveCount(0)
  expect(await page.evaluate(() => ({ ...localStorage }))).toEqual({ [key]: 'current' })
  await page.locator('input[type=file]').setInputFiles(photo)
  f.respond(route => stream(route, [done]))
  await page.getByRole('button', { name: 'Resume request' }).click()
  await expect(page.getByRole('status')).toHaveText('Ready')
  await expect(page.getByRole('img', { name: 'attachment' })).toHaveCount(0)
  expect(f.posts).toHaveLength(1)
  expect(f.posts[0].postData()).toContain('name="photo"; filename="fresh.png"')
  expect(f.posts[0].postData()).toContain('name="execution_id"\r\n\r\nwork')
  expect(f.posts[0].postData()).not.toContain('name="message"')
})

for (const approved of [true, false]) {
  test(`${approved ? 'approval' : 'denial'} renders its saved decision after reload`, async ({ context, page }) => {
    const f = await fixture(context, [user, proposal, waiting])
    await f.open(page)
    await expect(page.getByRole('status')).toHaveText('Awaiting approval')
    await expect(page.getByRole('button', { name: 'Resume request' })).toHaveCount(0)
    const decision = event(4, 'approval_decision', { execution_id: 'work', request_id: 'review', approved, tool: 'delete_part' })
    const completed = event(5, 'completed', { execution_id: 'work', status: 'completed' })
    f.respond(route => { f.histories.set('current', [user, proposal, waiting, decision, completed]); return stream(route, [decision, completed]) })
    await page.getByRole('button', { name: approved ? 'Approve' : 'Decline', exact: true }).click()
    await expect(page.getByText(approved ? 'Approved' : 'Declined', { exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByText(approved ? 'Approved' : 'Declined', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Decline', exact: true })).toHaveCount(0)
    expect(f.posts).toHaveLength(1)
    expect(f.posts[0].postDataJSON()).toEqual({ request_id: 'review', approved })
  })
}

test('tabs share a browser pointer but keep active histories and drafts independent', async ({ context, page }) => {
  const f = await fixture(context, [user, done])
  await f.open(page)
  const second = await context.newPage()
  await second.goto('/')
  await expect(second.getByRole('status')).toHaveText('Ready')
  await second.getByRole('textbox', { name: 'Message' }).fill('Second tab draft')
  await chooseConversation(page, 'other')
  await expect(page.getByText('Other conversation', { exact: true })).toBeVisible()
  await expect(second.getByRole('button', { name: 'Conversation', exact: true })).toHaveText('current▾')
  await second.getByRole('button', { name: 'Refresh history' }).click()
  await expect(second.getByRole('button', { name: 'Conversation', exact: true })).toHaveText('current▾')
  await expect(second.getByRole('textbox', { name: 'Message' })).toHaveValue('Second tab draft')
  // A stale approval in one tab must discover the other tab's saved outcome on refresh.
  f.histories.set('current', [user, proposal, waiting])
  await second.getByRole('button', { name: 'Refresh history' }).click()
  await chooseConversation(page, 'current')
  f.respond(route => {
    const decision = event(4, 'approval_decision', { execution_id: 'work', request_id: 'review', approved: true, tool: 'delete_part' })
    const completed = event(5, 'completed', { execution_id: 'work', status: 'completed' })
    f.histories.set('current', [user, proposal, waiting, decision, completed])
    return stream(route, [decision, completed])
  })
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByText('Approved', { exact: true })).toBeVisible()
  await second.getByRole('button', { name: 'Refresh history' }).click()
  await expect(second.getByText('Approved', { exact: true })).toBeVisible()
  await expect(second.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0)
  await expect(second.getByRole('textbox', { name: 'Message' })).toHaveValue('Second tab draft')
  expect(f.posts).toHaveLength(1)
})

test('retired history is selectable and read-only, while new chat clears attachments', async ({ context, page }) => {
  const f = await fixture(context, [user, done])
  await f.open(page)
  await page.getByRole('textbox', { name: 'Message' }).fill('Draft for current chat')
  await page.locator('input[type=file]').setInputFiles(photo)
  await chooseConversation(page, 'retired')
  await expect(page.getByText('Historical provider message', { exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('Read-only conversation')
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeDisabled()
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Attach photo' })).toBeDisabled()
  await expect(page.getByRole('img', { name: 'attachment' })).toHaveCount(0)
  await page.getByRole('button', { name: 'New chat', exact: true }).click()
  await expect(page.getByRole('status')).toHaveText('New conversation')
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeEnabled()
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBeNull()
  expect(f.posts).toHaveLength(0)
})

test('pending decisions disable competing controls until the response completes', async ({ context, page }) => {
  const f = await fixture(context, [user, proposal, waiting])
  await f.open(page)
  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  f.respond(async route => {
    await held
    await stream(route, [event(4, 'approval_decision', { execution_id: 'work', request_id: 'review', approved: true }),
      event(5, 'completed', { execution_id: 'work', status: 'completed' })])
  })
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  try {
    await expect(page.getByRole('status')).toHaveText('Working…')
    for (const name of ['Approve', 'Decline', 'Refresh history', 'New chat', 'Send message', 'Attach photo']) {
      await expect(page.getByRole('button', { name, exact: true })).toBeDisabled()
    }
    await expect(page.getByRole('button', { name: 'Conversation', exact: true })).toBeDisabled()
    await expect(page.getByRole('textbox', { name: 'Message' })).toBeDisabled()
    expect(f.posts).toHaveLength(1)
  } finally { release() }
  await expect(page.getByText('Approved', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Refresh history' })).toBeEnabled()
})

test('failed conversation selection preserves the active history, draft, and photo', async ({ context, page }) => {
  const f = await fixture(context, [user, done])
  await f.open(page)
  await page.getByRole('textbox', { name: 'Message' }).fill('Keep this draft')
  await page.locator('input[type=file]').setInputFiles(photo)
  f.failHistory(true)
  await chooseConversation(page, 'other')
  await expect(page.getByRole('alert')).toHaveText('History temporarily unavailable')
  await expect(page.getByRole('button', { name: 'Conversation', exact: true })).toHaveText('current▾')
  await expect(page.getByText('Add a resistor', { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('Keep this draft')
  await expect(page.getByRole('img', { name: 'attachment' })).toBeVisible()
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe('current')
  expect(f.posts).toHaveLength(0)
})

test('long history stays bounded, searchable, and keyboard accessible', async ({ context, page }, testInfo) => {
  const f = await fixture(context, [user, done])
  for (let index = 0; index < 100; index++) {
    const id = `archived-${index}`
    f.histories.set(id, [event(1, 'assistant_text', { text: `Saved message ${index}` }, id, 'codex')])
  }
  await f.open(page)
  await page.getByRole('button', { name: 'Conversation', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Conversation history' })
  const search = page.getByRole('searchbox', { name: 'Search conversations' })
  await expect(search).toBeFocused()
  const box = await panel.boundingBox()
  expect(box!.height).toBeLessThanOrEqual(420)
  await page.screenshot({ path: testInfo.outputPath('conversation-history.png') })
  expect(await panel.getByRole('button', { name: 'archived-99 codex · Read-only', exact: true }).count()).toBe(1)
  await search.fill('ARCHIVED-99')
  await expect(panel.getByRole('button')).toHaveCount(2)
  await expect(panel.getByText('1 of 103 conversations')).toBeVisible()
  await search.press('Tab')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Saved message 99', { exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('Read-only conversation')
  await expect(panel).toHaveCount(0)
  await page.getByRole('button', { name: 'Conversation', exact: true }).click()
  await expect(search).toHaveValue('')
  await search.fill('no such conversation')
  await expect(panel.getByText('No matching conversations.')).toBeVisible()
  await search.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Conversation', exact: true })).toBeFocused()
  await page.getByRole('button', { name: 'Conversation', exact: true }).click()
  await page.getByRole('button', { name: 'Refresh history' }).click()
  await expect(panel).toHaveCount(0)
  expect(f.posts).toHaveLength(0)
})

test('inventory routes restore parts and chat drafts with Back, Forward, and reload', async ({ context, page }) => {
  const part = { id: 42, part_category: 'op-amp', profile: 'discrete_ic', part_number: 'UA741CP', package: 'SOIC', quantity: 11 }
  const other = { ...part, id: 43, package: 'DIP' }
  const result = event(2, 'tool_result', { name: 'search_parts', result: { ok: true, result: { parts: [part] } } })
  const f = await fixture(context, [user, result, done])
  await context.route('**/inventory**', route => route.fulfill({
    json: new URL(route.request().url()).pathname === '/inventory/pending' ? { reviews: {} } : [part, other],
  }))
  await f.open(page)
  await page.getByRole('textbox', { name: 'Message' }).fill('Keep my draft')
  await page.getByRole('link', { name: 'Inventory', exact: true }).click()
  await expect(page).toHaveURL(/\/#\/inventory$/)
  await expect(page.getByRole('cell', { name: 'DIP', exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('Keep my draft')
  await page.goForward()
  await expect(page.getByRole('cell', { name: 'DIP', exact: true })).toBeVisible()
  await page.goBack()
  await page.getByRole('button', { name: 'View in inventory', exact: true }).click()
  await expect(page).toHaveURL(/\/#\/inventory\/42$/)
  await expect(page.getByText('Viewing part #42')).toBeVisible()
  await page.getByRole('button', { name: 'Show all parts' }).click()
  await expect(page).toHaveURL(/\/#\/inventory$/)
  await expect(page.getByRole('cell', { name: 'DIP', exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByText('Viewing part #42')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'DIP', exact: true })).toHaveCount(0)
  await page.goBack()
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('Keep my draft')
  await page.goForward()
  await expect(page.getByText('Viewing part #42')).toBeVisible()
  await page.reload()
  await expect(page.getByText('Viewing part #42')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'SOIC', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Inventory', exact: true })).toHaveAttribute('aria-current', 'page')
  expect(f.posts).toHaveLength(0)
})

test('direct inventory links survive reload and malformed part routes are rejected', async ({ context, page }) => {
  await fixture(context, [])
  await context.route('**/inventory**', route => route.fulfill({
    json: new URL(route.request().url()).pathname === '/inventory/pending' ? { reviews: {} } : [],
  }))
  await page.goto('/#/inventory/99')
  await expect(page.getByText('Viewing part #99')).toBeVisible()
  await expect(page.getByText('This part is no longer in inventory.')).toBeVisible()
  await page.goto('/#/inventory')
  await expect(page.getByText('No parts found.')).toBeVisible()
  await page.reload()
  await expect(page.getByText('No parts found.')).toBeVisible()
  for (const path of ['garbage', '0', '-1', '1.5', '9007199254740992']) {
    await page.goto(`/#/inventory/${path}`)
    await expect(page.getByRole('alert')).toContainText('Page not found')
  }
  await page.getByRole('link', { name: 'Chat', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible()
})
