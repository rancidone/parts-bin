import { useEffect, useRef, useState } from 'react'
import { SpecificationResult } from './SpecificationFacts'
import type { SpecificationState } from './SpecificationFacts'
import styles from './SpecificationSearch.module.css'

type Field = { unit: string | null; basis: string; choices: string[] }
type Contract = { category: string; fields: Record<string, Field>; minimum_source_conditions: Record<string, string[][]>; value_policy?: string; numeric_value_policy: string; conditions_policy: string; evidence_policy: string }
type Requirement = { name: string; comparison: string; value: string; conditionsText: string }
const label = (value: string) => value.replaceAll('_', ' ')
const blank = (contract: Contract): Requirement => ({ name: Object.keys(contract.fields)[0], comparison: 'eq', value: '', conditionsText: '' })

export function SpecificationSearch() {
  const [contracts, setContracts] = useState<Contract[]>([])
  const [category, setCategory] = useState('')
  const [requirements, setRequirements] = useState<Requirement[]>([])
  const [minimumQuantity, setMinimumQuantity] = useState('0')
  const [nominalValue, setNominalValue] = useState('')
  const passiveCategory = ['resistor', 'capacitor', 'inductor'].includes(category)
  const [result, setResult] = useState<SpecificationState | null>(null)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const contract = contracts.find(item => item.category === category)

  useEffect(() => {
    const controller = new AbortController()
    const counter = generation
    fetch('/inventory/specification-contracts', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Could not load electrical filters')
      const data: { categories: Contract[] } = await response.json()
      setContracts(data.categories)
      if (data.categories.length) {
        setCategory(data.categories[0].category)
        setRequirements([blank(data.categories[0])])
      }
    }).catch(error => { if (!controller.signal.aborted) setError(String(error)) })
    return () => { controller.abort(); counter.current++ }
  }, [])

  function invalidate() {
    generation.current++
    setBusy(false)
    setResult(null)
    setNextOffset(null)
    setError('')
  }
  function update(index: number, change: Partial<Requirement>) {
    invalidate()
    setRequirements(previous => previous.map((item, i) => i === index ? { ...item, ...change } : item))
  }
  async function search(offset = 0) {
    if (!contract) return
    const version = ++generation.current
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const parsed = requirements.map(item => {
        const conditions: Record<string, string> = {}
        for (const line of item.conditionsText.split('\n').filter(line => line.trim())) {
          const separator = line.indexOf('=')
          const key = line.slice(0, separator).trim()
          const value = line.slice(separator + 1).trim()
          if (separator < 1 || !value || Object.hasOwn(conditions, key)) throw new Error('Use one unique condition=value per line.')
          conditions[key] = value
        }
        return { name: item.name, comparison: item.comparison, value: item.value, basis: contract.fields[item.name].basis, conditions }
      })
      const response = await fetch('/inventory/specification-search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: { part_category: category, ...(passiveCategory && nominalValue.trim() ? { value: nominalValue.trim() } : {}) }, requirements: parsed, minimum_quantity: Number(minimumQuantity), limit: 20, offset }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail?.message ?? data.detail ?? response.statusText)
      if (version !== generation.current) return
      setResult(data)
      setNextOffset(data.next_offset)
    } catch (error) {
      if (version === generation.current) setError(String(error))
    } finally {
      if (version === generation.current) setBusy(false)
    }
  }

  return <section className={styles.panel} aria-label="Electrical inventory filters">
    <form onSubmit={event => { event.preventDefault(); void search() }}>
      <div className={styles.row}>
        <label>Electrical category<select value={category} onChange={event => {
          invalidate(); setCategory(event.target.value); setNominalValue('')
          setRequirements([blank(contracts.find(item => item.category === event.target.value)!)])
        }}>{contracts.map(item => <option key={item.category}>{item.category}</option>)}</select></label>
        <label>Minimum stock<input type="number" min="0" step="1" required value={minimumQuantity} onChange={event => { invalidate(); setMinimumQuantity(event.target.value) }} /></label>
      </div>
      {passiveCategory && <label>Nominal value (optional)<input value={nominalValue} placeholder={category === 'resistor' ? 'e.g. 10 kΩ' : category === 'capacitor' ? 'e.g. 100 nF' : 'e.g. 10 µH'} onChange={event => { invalidate(); setNominalValue(event.target.value) }} /></label>}
      {contract && <>
        <p>{contract.evidence_policy} {contract.conditions_policy}</p>
        {contract.value_policy && <p>{contract.value_policy}</p>}
        <p>{contract.numeric_value_policy}</p>
        <p>Partial matches show nearby nominal values or failed electrical requirements. Review each difference before choosing an alternative.</p>
        {requirements.map((item, index) => {
          const field = contract.fields[item.name]
          const qualifiers = contract.minimum_source_conditions[item.name] ?? []
          return <fieldset key={index}>
            <legend>Requirement {index + 1}</legend>
            <div className={styles.row}>
              <label>Specification<select value={item.name} onChange={event => update(index, { name: event.target.value, comparison: 'eq', value: '', conditionsText: '' })}>{Object.keys(contract.fields).map(name => <option value={name} key={name}>{label(name)}</option>)}</select></label>
              <label>Comparison<select value={item.comparison} onChange={event => update(index, { comparison: event.target.value })}><option value="eq">Equal to</option>{field.unit && <><option value="gte">At least</option><option value="lte">At most</option></>}</select></label>
              <label>Required value{field.choices.length ? <select required value={item.value} onChange={event => update(index, { value: event.target.value })}><option value="">Choose value</option>{field.choices.map(choice => <option key={choice}>{choice}</option>)}</select> : <input required value={item.value} placeholder={field.unit ? `Include units, e.g. 5 ${field.unit}` : 'Exact value'} onChange={event => update(index, { value: event.target.value })} />}</label>
            </div>
            <p>Rating: {label(field.basis)}{qualifiers.length > 0 && ` · Evidence requires: ${qualifiers.map(group => group.join(' or ')).join(', ')}`}</p>
            <label>Conditions (one condition=value per line)<textarea value={item.conditionsText} placeholder="ambient_temperature=25 °C" onChange={event => update(index, { conditionsText: event.target.value })} /></label>
            {requirements.length > 1 && <button type="button" onClick={() => { invalidate(); setRequirements(previous => previous.filter((_, i) => i !== index)) }}>Remove requirement {index + 1}</button>}
          </fieldset>
        })}
        <div className={styles.row}>
          <button type="button" disabled={requirements.length >= 20} onClick={() => { invalidate(); setRequirements(previous => [...previous, blank(contract)]) }}>Add requirement</button>
          <button disabled={busy} type="submit">{busy ? 'Searching…' : 'Search accepted specifications'}</button>
        </div>
      </>}
    </form>
    {error && <p role="alert">{error}</p>}
    {result && <><SpecificationResult value={{ ...result, truncated: false }} />{nextOffset !== null && <button disabled={busy} onClick={() => void search(nextOffset)}>Next results</button>}</>}
  </section>
}
