import assert from 'node:assert/strict'
import { test } from 'node:test'
import { AgentSession, approvalDecision, unfinishedExecutions, readEventStream } from '../src/agentSession.ts'

const event = (sequence, kind, data = {}, runtime = 'openai') => ({ kind, sequence, data, runtime, thread_id: 'thread' })
const user = event(1, 'user_message', { text: 'hello', execution_id: 'work' })
const done = event(3, 'completed', { status: 'completed', execution_id: 'work' })
const encode = events => events.map(item => `event: agent_event\ndata: ${JSON.stringify(item)}\n\n`).join('')
const response = events => new Response(encode(events))
function storage() {
  const data = new Map()
  return { data, getItem: key => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) }
}
function fixture(history = []) {
  const saved = storage()
  saved.setItem('parts-bin.current-thread', 'thread')
  const requests = []
  let reply = async () => response(history)
  const fetcher = async (url, init) => { requests.push({ url, init }); return reply(url, init) }
  const session = new AgentSession(fetcher, saved)
  return { session, saved, requests, respond: callback => { reply = callback } }
}

test('parses split UTF-8, CRLF, comments, and a final frame without a separator', async () => {
  const item = event(1, 'assistant_text', { text: '10 kΩ 🔧' })
  const bytes = new TextEncoder().encode(': heartbeat\r\n\r\n' + encode([item]).trim().replaceAll('\n', '\r\n'))
  const stream = new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
    controller.close()
  } })
  const received = []
  await readEventStream(new Response(stream), item => received.push(item))
  assert.deepEqual(received, [item])
})

test('malformed events stop processing rather than hiding missing history', async () => {
  await assert.rejects(readEventStream(new Response('event: agent_event\ndata: {\n\n'), () => {}))
  await assert.rejects(readEventStream(response([{ ...user, sequence: null }]), () => {}), /invalid chat event/)
})

test('reload and repeated replay restore ordered history without duplicate messages or POSTs', async () => {
  const f = fixture([done, user, user, event(2, 'assistant_text', { text: 'done', execution_id: 'work' })])
  await f.session.restore()
  await f.session.refresh()
  assert.deepEqual(f.session.getSnapshot().events.map(item => item.sequence), [1, 2, 3])
  assert.equal(f.session.getSnapshot().restored, true)
  assert.equal(unfinishedExecutions(f.session.getSnapshot().events).length, 0)
  assert.ok(f.requests.every(item => item.url.endsWith('/events') && !item.init?.method))
  const reloaded = new AgentSession(async () => response([user, done]), f.saved)
  await reloaded.restore()
  assert.equal(reloaded.getSnapshot().threadId, 'thread')
})

test('new messages save only the conversation ID and replace their optimistic echo', async () => {
  const saved = storage()
  const requests = []
  const session = new AgentSession(async (url, init) => {
    requests.push({ url, init })
    return url === '/agent/threads' ? Response.json({ thread_id: 'thread' }) : response([user, done])
  }, saved)
  const photo = new File(['private image bytes'], 'part.png', { type: 'image/png' })
  assert.equal(await session.submit('hello', photo), true)
  assert.deepEqual([...saved.data.values()], ['thread'])
  assert.equal(requests[1].init.body.get('photo').name, 'part.png')
  assert.equal(session.getSnapshot().events.filter(item => item.kind === 'user_message').length, 1)
  assert.ok(session.getSnapshot().events.every(item => item.sequence > 0))
})

test('failed restore retains its ID and blocks messages until history is recovered', async () => {
  const f = fixture()
  f.respond(async () => Response.json({ detail: 'Unknown conversation thread' }, { status: 404 }))
  await f.session.restore()
  assert.equal(f.session.getSnapshot().threadId, 'thread')
  assert.equal(f.session.getSnapshot().restored, false)
  await f.session.submit('do not send')
  assert.equal(f.requests.length, 1)
  f.respond(async () => response([]))
  assert.equal(await f.session.refresh(), true)
  assert.equal(f.session.getSnapshot().restored, true)
})

test('truncated mutation streams require read-only recovery and never resubmit automatically', async () => {
  const f = fixture([])
  await f.session.restore()
  f.respond(async () => response([user]))
  assert.equal(await f.session.submit('hello'), false)
  assert.match(f.session.getSnapshot().error, /interrupted/)
  assert.equal(f.session.getSnapshot().restored, false)
  const requests = f.requests.length
  assert.equal(await f.session.submit('hello'), false)
  assert.equal(f.requests.length, requests)
  f.respond(async () => response([user]))
  await f.session.refresh()
  assert.equal(unfinishedExecutions(f.session.getSnapshot().events)[0].id, 'work')
  assert.equal(f.requests.filter(item => item.init?.method === 'POST').length, 1)
})

test('one guard prevents overlapping submit, approval, resume, and refresh', async () => {
  const f = fixture([user])
  await f.session.restore()
  let release
  f.respond(() => new Promise(resolve => { release = resolve }))
  const deciding = f.session.decide('approval', true)
  const count = f.requests.length
  assert.equal(await f.session.submit('overlap'), false)
  assert.equal(await f.session.decide('approval', false), false)
  assert.equal(await f.session.resume('work'), false)
  assert.equal(await f.session.refresh(), false)
  f.session.newChat()
  assert.equal(f.session.getSnapshot().threadId, 'thread')
  assert.equal(f.requests.length, count)
  release(response([done]))
  await deciding
  assert.equal(f.session.getSnapshot().pending, false)
})

for (const approved of [true, false]) {
  test(`resolved ${approved ? 'approval' : 'denial'} cannot submit another decision`, async () => {
    const proposal = event(2, 'approval_request', { execution_id: 'work', request_id: 'review' })
    const pause = event(3, 'completed', { execution_id: 'work', status: 'awaiting_approval' })
    const decision = event(4, 'approval_decision', { execution_id: 'work', request_id: 'review', approved })
    const f = fixture([user, proposal, pause])
    await f.session.restore()
    assert.equal(unfinishedExecutions(f.session.getSnapshot().events)[0].status, 'awaiting_approval')
    f.respond(async () => response([decision, event(5, 'completed', { execution_id: 'work', status: 'completed' })]))
    assert.equal(await f.session.decide('review', approved), true)
    assert.equal(approvalDecision(f.session.getSnapshot().events, 'review'), approved)
    const count = f.requests.length
    await f.session.decide('review', !approved)
    assert.equal(f.requests.length, count)
  })
}

test('explicit resume refreshes history first and sends the original execution ID', async () => {
  const f = fixture([user])
  await f.session.restore()
  f.respond(async (url, init) => {
    if (url.endsWith('/events')) return response([user])
    assert.equal(init.body.get('execution_id'), 'work')
    assert.equal(init.body.has('message'), false)
    return response([user, done])
  })
  assert.equal(await f.session.resume('work'), true)
  assert.deepEqual(f.requests.slice(1).map(item => item.url), ['/agent/threads/thread/events', '/agent/threads/thread/resume'])
  assert.equal(f.session.getSnapshot().events.length, 2)
})

test('resume does no paid work if replay discovers completion', async () => {
  const f = fixture([user])
  await f.session.restore()
  f.respond(async () => response([user, done]))
  await f.session.resume('work')
  assert.equal(f.requests.filter(item => item.init?.method === 'POST').length, 0)
})

test('initial image interruption requires a fresh photo and stores no image payload', async () => {
  const photoUser = event(1, 'user_message', { execution_id: 'work', image: { media_type: 'image/png' } })
  const f = fixture([photoUser])
  await f.session.restore()
  assert.equal(await f.session.resume('work'), false)
  assert.match(f.session.getSnapshot().error, /fresh photo/)
  assert.equal(f.requests.filter(item => item.init?.method === 'POST').length, 0)
  const photo = new File(['image'], 'new.png', { type: 'image/png' })
  f.respond(async (url, init) => {
    if (url.endsWith('/events')) return response([photoUser])
    assert.equal(init.body.get('photo').name, 'new.png')
    return response([done])
  })
  assert.equal(await f.session.resume('work', photo), true)
  assert.deepEqual([...f.saved.data.values()], ['thread'])
})

test('checkpointed photo tools and waiting approvals do not demand image replay', () => {
  const photoUser = event(1, 'user_message', { execution_id: 'work', image: { media_type: 'image/png' } })
  const call = event(2, 'tool_call', { execution_id: 'work' })
  assert.equal(unfinishedExecutions([photoUser, call])[0].needsPhoto, false)
})

test('gateway failure does not falsely mark an interrupted execution as terminal', () => {
  const failed = event(2, 'completed', { status: 'failed' })
  assert.equal(unfinishedExecutions([user, failed])[0].id, 'work')
  assert.equal(unfinishedExecutions([user, event(2, 'completed', { status: 'failed', execution_id: 'work' })]).length, 0)
})

test('retired-provider history is readable without issuing new provider work', async () => {
  const f = fixture([event(1, 'assistant_text', { text: 'old' }, 'codex')])
  await f.session.restore()
  assert.equal(await f.session.submit('continue'), false)
  assert.match(f.session.getSnapshot().error, /retired provider/)
  assert.equal(f.requests.length, 1)
})

test('new chat changes only the browser pointer and never deletes server history', async () => {
  const f = fixture([user, done])
  await f.session.restore()
  f.session.newChat()
  assert.equal(f.session.getSnapshot().threadId, null)
  assert.equal(f.saved.data.size, 0)
  assert.equal(f.requests.length, 1)
})

test('a server startup failure does not discard an unacknowledged draft as successful', async () => {
  const f = fixture([])
  await f.session.restore()
  f.respond(async () => response([event(1, 'error', { code: 'runtime_startup_failed', message: 'Unavailable' }),
    event(2, 'completed', { status: 'failed' })]))
  assert.equal(await f.session.submit('retain this draft'), false)
  assert.match(f.session.getSnapshot().error, /not acknowledged/)
})

test('storage being disabled still allows an in-memory conversation', async () => {
  const saved = { getItem() { throw new Error('Disabled') }, setItem() { throw new Error('Disabled') } }
  const session = new AgentSession(async url => url === '/agent/threads' ? Response.json({ thread_id: 'thread' }) : response([user, done]), saved)
  assert.equal(await session.submit('hello'), true)
  assert.equal(session.getSnapshot().threadId, 'thread')
})

test('an interrupted approval response does not send a second decision automatically', async () => {
  const proposal = event(2, 'approval_request', { execution_id: 'work', request_id: 'review' })
  const f = fixture([user, proposal])
  await f.session.restore()
  f.respond(async () => response([event(3, 'approval_decision', { execution_id: 'work', request_id: 'review', approved: true })]))
  assert.equal(await f.session.decide('review', true), false)
  assert.equal(f.session.getSnapshot().restored, false)
  assert.equal(approvalDecision(f.session.getSnapshot().events, 'review'), true)
  assert.equal(f.requests.filter(item => item.init?.method === 'POST').length, 1)
})
