import test from 'node:test'
import assert from 'node:assert/strict'
import { partsToCSV } from '../src/csv.ts'

test('nominal export retains decimal base values and their dimensions', () => {
  const csv = partsToCSV([{ part_category: 'capacitor', value: '0.0000001', value_unit: 'F', display_value: '100nF',
    package: '0603', quantity: 20, part_number: null, manufacturer: null, description: null }])
  assert.ok(csv.startsWith('part_category,value,value_unit,package,'))
  assert.ok(csv.includes('capacitor,0.0000001,F,0603,20,'))
  assert.ok(!csv.includes('100nF'))
})
