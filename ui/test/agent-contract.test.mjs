import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const source = await readFile(new URL('../src/useAgent.ts', import.meta.url), 'utf8')
const chat = await readFile(new URL('../src/Chat.tsx', import.meta.url), 'utf8')

test('UI has one normalized event-stream reader and only agent conversation endpoints', () => {
  assert.equal((source.match(/async function readEventStream/g) ?? []).length, 1)
  assert.match(source, /\/agent\/threads/)
  assert.doesNotMatch(source, /\/(?:chat|query)|\/settings\/llm/)
  assert.doesNotMatch(chat, /\/(?:chat|query)|\/settings\/llm/)
})

test('runtime selector is locked once a thread exists', () => {
  assert.match(chat, /disabled=\{Boolean\(threadId\)\}/)
})

for (const kind of ['user_message', 'assistant_text', 'tool_call', 'tool_result', 'approval_request', 'approval_decision', 'error']) {
  test(`renders ${kind} event data`, async () => {
    assert.match(chat, new RegExp(`event\.kind === ['"]${kind}['"]`))
  })
}
