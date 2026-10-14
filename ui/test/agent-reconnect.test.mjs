import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'
import { test } from 'node:test'
import { AgentSession } from '../src/agentSession.ts'

const event = (sequence, kind, data, thread_id = 'thread') => ({ sequence, kind, data, thread_id, runtime: 'openai' })
const frames = events => events.map(value => `event: agent_event\ndata: ${JSON.stringify(value)}\n\n`).join('')
const replay = (events, active = false, accepted = true) => new Response(frames(events), {
  headers: { 'X-Agent-Active': String(active), 'X-Execution-Accepted': String(accepted) },
})
const user = event(1, 'user_message', { text: 'hello', execution_id: 'work' })
const answer = event(2, 'assistant_text', { text: 'saved answer', execution_id: 'work' })
const done = event(3, 'completed', { status: 'completed', execution_id: 'work' })
const summaries = () => Response.json({ threads: ['thread', 'other'].map(thread_id => ({ thread_id, runtime: 'openai', title: thread_id, last_sequence: 3 })) })
const storage = () => {
  const values = new Map([['parts-bin.current-thread', 'thread']])
  return { values, getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
}
const flush = async () => { await setImmediate(); await setImmediate() }
function setup(t, responder) {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const requests = []
  const saved = storage()
  const session = new AgentSession(async (url, init) => {
    requests.push({ url, init })
    return url === '/agent/threads' ? summaries() : responder(url, init)
  }, saved)
  t.after(() => session.disconnect())
  return { session, requests, saved }
}

test('a lost intake acknowledgement reconnects from the stored cursor through outages, without another POST', async t => {
  let executionId
  let reads = 0
  const f = setup(t, (url, init) => {
    if (init?.method === 'POST') {
      executionId = init.body.get('execution_id')
      throw new TypeError('network lost after acceptance')
    }
    if (!url.includes('?')) return replay([])
    reads++
    const query = new URL(url, 'http://test').searchParams
    if (reads === 1) {
      assert.equal(query.get('execution_id'), executionId)
      assert.equal(query.get('after'), '0')
      return replay([event(1, 'user_message', { text: 'hello', execution_id: executionId })], true)
    }
    assert.equal(query.get('after'), '1')
    if (reads === 2) throw new TypeError('offline')
    if (reads === 3) return Response.json({ detail: 'Redeploying' }, { status: 503 })
    return replay([event(2, 'assistant_text', { text: 'saved answer', execution_id: executionId }),
      event(3, 'completed', { status: 'completed', execution_id: executionId })])
  })
  await f.session.restore()
  assert.equal(await f.session.submit('hello'), true)
  assert.equal(f.session.getSnapshot().watching, true)
  t.mock.timers.tick(1000); await flush()
  assert.equal(f.session.getSnapshot().reconnecting, true)
  t.mock.timers.tick(2000); await flush()
  assert.equal(reads, 3)
  t.mock.timers.tick(4000); await flush()
  assert.equal(f.session.getSnapshot().watching, false)
  assert.equal(f.session.getSnapshot().reconnecting, false)
  assert.equal(f.session.getSnapshot().error, null)
  assert.deepEqual(f.session.getSnapshot().events.map(value => value.sequence), [1, 2, 3])
  assert.equal(f.requests.filter(value => value.init?.method === 'POST').length, 1)
  assert.deepEqual([...f.saved.values.values()], ['thread'])
})

test('reload follows active photo work with GETs and stops after completion', async t => {
  const photoUser = { ...user, data: { ...user.data, image: { media_type: 'image/png' } } }
  const f = setup(t, url => url.includes('?') ? replay([answer, done]) : replay([photoUser], true))
  await f.session.restore()
  assert.equal(f.session.getSnapshot().watching, true)
  t.mock.timers.tick(1000); await flush()
  assert.equal(f.requests.at(-1).url, '/agent/threads/thread/events?after=1')
  assert.equal(f.session.getSnapshot().watching, false)
  assert.ok(f.requests.every(value => !value.init?.method))
  const count = f.requests.length
  t.mock.timers.tick(60000); await flush()
  assert.equal(f.requests.length, count)
  assert.deepEqual([...f.saved.values.values()], ['thread'])
})

test('switching conversations aborts the old observer and ignores its late response', async t => {
  let release
  let signal
  const other = event(1, 'assistant_text', { text: 'other history' }, 'other')
  const f = setup(t, (url, init) => {
    if (url.includes('/other/')) return replay([other])
    if (!url.includes('?')) return replay([user], true)
    signal = init.signal
    return new Promise(resolve => { release = resolve })
  })
  await f.session.restore()
  t.mock.timers.tick(1000); await flush()
  assert.equal(await f.session.selectConversation('other'), true)
  assert.equal(signal.aborted, true)
  release(replay([answer, done]))
  await flush()
  assert.deepEqual(f.session.getSnapshot().events, [other])
  assert.equal(f.session.getSnapshot().threadId, 'other')
  assert.equal(f.session.getSnapshot().watching, false)
  assert.ok(f.requests.every(value => !value.init?.method))
})

test('a stream ending midway through a frame recovers from the last complete event', async t => {
  let executionId
  const f = setup(t, (url, init) => {
    if (!init?.method) {
      if (!url.includes('?')) return replay([])
      assert.equal(new URL(url, 'http://test').searchParams.get('after'), '1')
      return replay([event(2, 'assistant_text', { text: 'whole answer', execution_id: executionId }),
        event(3, 'completed', { status: 'completed', execution_id: executionId })])
    }
    executionId = init.body.get('execution_id')
    const bytes = new TextEncoder().encode(frames([event(1, 'user_message', { text: 'hello', execution_id: executionId })]) + 'event: agent_event\ndata: {"kind": "assistant_text", "data": {"text": "cut')
    return new Response(new ReadableStream({ start(controller) { controller.enqueue(bytes); controller.close() } }))
  })
  await f.session.restore()
  assert.equal(await f.session.submit('hello'), true)
  assert.deepEqual(f.session.getSnapshot().events.map(value => value.sequence), [1, 2, 3])
  assert.equal(f.requests.filter(value => value.init?.method === 'POST').length, 1)
})

test('authorization failure stops reconnecting and keeps saved history', async t => {
  const f = setup(t, url => url.includes('?') ? Response.json({ detail: 'Sign in again' }, { status: 401 }) : replay([user], true))
  await f.session.restore()
  t.mock.timers.tick(1000); await flush()
  assert.equal(f.session.getSnapshot().watching, false)
  assert.equal(f.session.getSnapshot().restored, false)
  assert.match(f.session.getSnapshot().error, /Sign in again/)
  assert.deepEqual(f.session.getSnapshot().events, [user])
  const count = f.requests.length
  t.mock.timers.tick(60000); await flush()
  assert.equal(f.requests.length, count)
})

test('a never-accepted POST retains its draft and is not submitted again', async t => {
  const f = setup(t, (url, init) => {
    if (init?.method === 'POST') throw new TypeError('upload never delivered')
    return replay([], false, false)
  })
  await f.session.restore()
  assert.equal(await f.session.submit('keep draft'), false)
  assert.match(f.session.getSnapshot().error, /not acknowledged/)
  assert.equal(f.session.getSnapshot().events[0].data.text, 'keep draft')
  assert.equal(f.session.getSnapshot().watching, false)
  assert.equal(f.requests.filter(value => value.init?.method === 'POST').length, 1)
})

test('approval recovery reads a saved decision and never approves twice', async t => {
  const proposal = event(2, 'approval_request', { execution_id: 'work', request_id: 'review' })
  const paused = event(3, 'completed', { execution_id: 'work', status: 'awaiting_approval' })
  let reads = 0
  const decision = event(4, 'approval_decision', { execution_id: 'work', request_id: 'review', approved: true })
  const f = setup(t, (url, init) => {
    if (init?.method === 'POST') throw new TypeError('lost decision acknowledgement')
    if (!url.includes('?')) return replay([user, proposal, paused])
    reads++
    assert.equal(new URL(url, 'http://test').searchParams.get('after'), reads === 1 ? '3' : '4')
    return reads === 1 ? replay([decision], true) : replay([event(5, 'completed', { status: 'completed', execution_id: 'work' })])
  })
  await f.session.restore()
  assert.equal(await f.session.decide('review', true), true)
  t.mock.timers.tick(1000); await flush()
  assert.equal(f.session.getSnapshot().watching, false)
  assert.equal(f.requests.filter(value => value.init?.method === 'POST').length, 1)
})


test('a stalled progress connection closes and recovers by GET without another POST', async t => {
  let executionId
  let cancelled = false
  const f = setup(t, (url, init) => {
    if (!init?.method) return !url.includes('?') ? replay([]) : replay([
      event(2, 'assistant_text', { text: 'recovered after stall', execution_id: executionId }),
      event(3, 'completed', { status: 'completed', execution_id: executionId })])
    executionId = init.body.get('execution_id')
    return new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode(frames([
        event(1, 'user_message', { text: 'hello', execution_id: executionId })]))) },
      cancel() { cancelled = true },
    }))
  })
  await f.session.restore()
  const submitted = f.session.submit('hello')
  await flush()
  t.mock.timers.tick(15000); await flush()
  assert.equal(await submitted, true)
  assert.equal(cancelled, true)
  assert.equal(new URL(f.requests.at(-1).url, 'http://test').searchParams.get('after'), '1')
  assert.equal(f.requests.filter(value => value.init?.method === 'POST').length, 1)
})


test('a foreign-thread replay is rejected without advancing the active cursor', async t => {
  const f = setup(t, url => url.includes('?') ? replay([event(2, 'assistant_text', { text: 'wrong' }, 'other')]) : replay([user], true))
  await f.session.restore()
  t.mock.timers.tick(1000); await flush()
  assert.equal(f.session.getSnapshot().watching, false)
  assert.equal(f.session.getSnapshot().restored, false)
  assert.match(f.session.getSnapshot().error, /another conversation/)
  assert.deepEqual(f.session.getSnapshot().events, [user])
})


test('cold reload retries unavailable history before following active work, using GETs only', async t => {
  let initialReads = 0
  const f = setup(t, url => {
    if (url.includes('?')) return replay([answer, done])
    if (++initialReads === 1) throw new TypeError('offline at reload')
    return replay([user], true)
  })
  const restoring = f.session.restore()
  await flush()
  assert.equal(f.session.getSnapshot().reconnecting, true)
  t.mock.timers.tick(1000); await flush()
  await restoring
  assert.equal(f.session.getSnapshot().restored, true)
  assert.equal(f.session.getSnapshot().watching, true)
  t.mock.timers.tick(1000); await flush()
  assert.equal(f.session.getSnapshot().watching, false)
  assert.ok(f.requests.every(value => !value.init?.method))
})


test('effect cleanup during restore can reconnect the same session without duplicate work', async t => {
  let release
  let signal
  let initialReads = 0
  const f = setup(t, (url, init) => {
    if (url.includes('?')) return replay([answer, done])
    if (++initialReads === 1) { signal = init.signal; return new Promise(resolve => { release = resolve }) }
    return replay([user], true)
  })
  const initial = f.session.restore()
  await flush()
  f.session.disconnect()
  assert.equal(signal.aborted, true)
  const reconnected = f.session.restore()
  release(replay([user], true))
  await initial; await reconnected
  assert.equal(initialReads, 2)
  assert.equal(f.session.getSnapshot().restored, true)
  t.mock.timers.tick(1000); await flush()
  assert.equal(f.session.getSnapshot().watching, false)
  assert.ok(f.requests.every(value => !value.init?.method))
})
