import type { Part } from './types'

const COLUMNS: (keyof Part)[] = [
  'part_category',
  'value',
  'package',
  'quantity',
  'part_number',
  'manufacturer',
  'description',
]

function escapeCell(val: unknown): string {
  const s = val == null ? '' : String(val)
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`
  }
  return s
}

export function partsToCSV(parts: Part[]): string {
  const header = COLUMNS.join(',')
  const rows = parts.map(p => COLUMNS.map(col => escapeCell(p[col])).join(','))
  return [header, ...rows].join('\n')
}

export function downloadCSV(parts: Part[], filename: string): void {
  const csv = partsToCSV(parts)
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
