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
    await expect(page.getByRole('combobox', { name: 'Conversation' })).toHaveValue('current')
    await expect(page.getByRole('button', { name: 'Refresh history' })).toBeEnabled()
  }
  return { histories, posts, open, respond: (handler: typeof write) => { write = handler },
    failHistory: (failed: boolean) => { historyUnavailable = failed } }
}

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
  await expect(page.getByText('Add a resistor', { exact: true })).toHaveCount(1)
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
  await page.getByRole('combobox', { name: 'Conversation' }).selectOption('other')
  await expect(page.getByText('Other conversation', { exact: true })).toBeVisible()
  await expect(second.getByRole('combobox', { name: 'Conversation' })).toHaveValue('current')
  await second.getByRole('button', { name: 'Refresh history' }).click()
  await expect(second.getByRole('combobox', { name: 'Conversation' })).toHaveValue('current')
  await expect(second.getByRole('textbox', { name: 'Message' })).toHaveValue('Second tab draft')
  // A stale approval in one tab must discover the other tab's saved outcome on refresh.
  f.histories.set('current', [user, proposal, waiting])
  await second.getByRole('button', { name: 'Refresh history' }).click()
  await page.getByRole('combobox', { name: 'Conversation' }).selectOption('current')
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
  await page.getByRole('combobox', { name: 'Conversation' }).selectOption('retired')
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
    await expect(page.getByRole('combobox', { name: 'Conversation' })).toBeDisabled()
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
  await page.getByRole('combobox', { name: 'Conversation' }).selectOption('other')
  await expect(page.getByRole('alert')).toHaveText('History temporarily unavailable')
  await expect(page.getByRole('combobox', { name: 'Conversation' })).toHaveValue('current')
  await expect(page.getByText('Add a resistor', { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('Keep this draft')
  await expect(page.getByRole('img', { name: 'attachment' })).toBeVisible()
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe('current')
  expect(f.posts).toHaveLength(0)
})
