import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const source = (await readFile(new URL('../src/SpecificationFacts.tsx', import.meta.url), 'utf8'))
  .replace("import styles from './SpecificationFacts.module.css'", 'const styles = new Proxy({}, { get: (_target, key) => String(key) })')
const javascript = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext } }).outputText
  .replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))
const { SpecificationResult, SpecificationReview } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)
const fact = { name: 'rated_power', value: '250 mW', basis: 'rated', conditions: { ambient_temperature: '25 °C' },
  evidence: { kind: 'source', url: 'https://example.com/datasheet.pdf', page: 2, excerpt: 'Rated power 250 mW at 25 °C.' } }
const part = { id: 1, part_category: 'resistor', profile: 'passive', value: '10k', package: '0603', part_number: 'EXACT,215', quantity: 5 }
const render = value => renderToStaticMarkup(createElement(SpecificationResult, { value }))

test('identity, stock, rating, conditions and evidence share a single result card', () => {
  const html = render({ matches: [{ part, supporting_facts: [fact] }], incomplete: [] })
  const card = html.match(/<article[\s\S]*?<\/article>/)?.[0]
  assert.ok(card)
  for (const text of ['EXACT,215', '5 in stock', 'Meets stated requirements', '250 mW', 'ambient temperature: 25 °C', 'View source evidence']) assert.ok(card.includes(text), text)
  assert.equal((html.match(/<article/g) ?? []).length, 1)
  assert.ok(!html.includes('ambient_temperature'))
  assert.ok(!html.includes('<details class="evidence" open'))
})

test('incomplete candidates remain distinct and display human-readable missing facts', () => {
  const html = render({ matches: [], incomplete: [{ part, missing_or_unqualified: ['rated_power', 'tolerance'] }] })
  assert.ok(html.includes('0 confirmed matches'))
  assert.ok(html.includes('Needs evidence or matching conditions'))
  assert.ok(html.includes('Not confirmed for: rated power, tolerance.'))
  assert.ok(!html.includes('Meets stated requirements'))
})

test('approval keeps evidence open and identifies the exact target', () => {
  const html = renderToStaticMarkup(createElement(SpecificationReview, { partId: 1,
    review: { facts: [fact], snapshot: { metadata: part } } }))
  assert.ok(html.includes('EXACT,215'))
  assert.ok(html.includes('Part #1'))
  assert.ok(html.includes('<details class="evidence" open="">'))
  assert.ok(html.includes('Rated power 250 mW at 25 °C.'))
})

test('approval displays every supporting source passage and its page', () => {
  const supported = { ...fact, evidence: { ...fact.evidence,
    supporting_passages: [{ page: 3, excerpt: 'Rating requires <70 °C and characteristic U.' }] } }
  const html = renderToStaticMarkup(createElement(SpecificationReview, { partId: 1,
    review: { facts: [supported], snapshot: { metadata: part } } }))
  assert.ok(html.includes('Supporting source passage · page 3'))
  assert.ok(html.includes('Rating requires &lt;70 °C and characteristic U.'))
  assert.ok(html.includes('Rated power 250 mW at 25 °C.'))
})

test('assertions remain labeled and passages are escaped', () => {
  const assertion = { ...fact, evidence: { kind: 'user_assertion', excerpt: '<script>untrusted()</script>' } }
  const html = render({ facts: [assertion], part_id: 1 })
  assert.ok(html.includes('User assertion'))
  assert.ok(html.includes('&lt;script&gt;'))
  assert.ok(!html.includes('<script>'))
  assert.ok(!html.includes('Source document'))
})

test('counts distinguish full results from the returned subset', () => {
  const html = render({ matches: [{ part, supporting_facts: [fact] }], incomplete: [], match_count: 25, incomplete_count: 3, truncated: true })
  assert.ok(html.includes('25 confirmed matches'))
  assert.ok(html.includes('3 need evidence or conditions'))
  assert.ok(html.includes('Showing 1 confirmed and 0 incomplete records.'))
})
