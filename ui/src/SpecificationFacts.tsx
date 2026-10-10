import type { Part } from './types'
import styles from './SpecificationFacts.module.css'

type Fact = { name: string; value: string; basis: string; conditions: Record<string, string>; evidence: { kind: string; excerpt: string; url?: string; page?: number } }
type Review = { facts: Fact[]; snapshot: { metadata: { part_number: string | null; package: string | null; part_category: string } } }
type Result = { matches?: { part: Part; supporting_facts: Fact[] }[]; incomplete?: { part: Part; missing_or_unqualified: string[] }[]; facts?: Fact[]; part_id?: number; pending_review?: Review | null; truncated?: boolean; match_count?: number; incomplete_count?: number }

const label = (text: string) => text.replaceAll('_', ' ')

export function SpecificationFacts({ facts, reviewing = false }: { facts: Fact[]; reviewing?: boolean }) {
  if (!facts.length) return <p className={styles.note}>No accepted electrical facts yet.</p>
  return <div className={styles.facts}>{facts.map(fact => <div className={styles.fact} key={fact.name}>
    <div className={styles.factHeading}><span>{label(fact.name)}</span><strong>{fact.value}</strong><span className={styles.basis}>{label(fact.basis)}</span></div>
    <div className={styles.conditions}>{Object.entries(fact.conditions).map(([key, value]) => `${label(key)}: ${value}`).join(' · ') || 'No conditions stated'}
      {fact.evidence.kind !== 'source' && <span className={styles.assertion}> · User assertion</span>}
    </div>
    <details className={styles.evidence} open={reviewing}>
      <summary>View {fact.evidence.kind === 'source' ? 'source evidence' : 'user assertion'}</summary>
      <div className={styles.evidenceBody}>
        {fact.evidence.kind === 'source' && <a href={fact.evidence.url} target="_blank" rel="noreferrer">Source document · page {fact.evidence.page}</a>}
        <blockquote>{fact.evidence.excerpt}</blockquote>
      </div>
    </details>
  </div>)}</div>
}

export function SpecificationReview({ review, partId }: { review: Review; partId: string | number }) {
  const identity = review.snapshot.metadata
  return <section className={styles.card} aria-label={`Specification review for part ${partId}`}>
    <div className={styles.identity}><strong>{identity.part_number ?? identity.part_category}</strong><span>Part #{partId} · {identity.package ?? 'Package unknown'}</span></div>
    <SpecificationFacts facts={review.facts} reviewing />
  </section>
}

function SpecificationMatch({ part, facts, missing }: { part: Part; facts?: Fact[]; missing?: string[] }) {
  const incomplete = missing !== undefined
  return <article className={styles.card} aria-label={`Part ${part.id}: ${incomplete ? 'needs evidence' : 'confirmed match'}`}>
    <div className={styles.cardHeader}>
      <div className={styles.identity}><strong>{[part.part_category, part.value, part.package].filter(Boolean).join(' · ')}</strong>
        <span>{part.part_number ?? 'Ordering code unknown'} · Part #{part.id}</span></div>
      <span className={styles.stock}>{part.quantity} in stock</span>
    </div>
    <span className={incomplete ? styles.incomplete : styles.confirmed}>{incomplete ? 'Needs evidence or matching conditions' : 'Meets stated requirements'}</span>
    {incomplete ? <p className={styles.note}>Not confirmed for: {missing.map(label).join(', ')}.</p> : <SpecificationFacts facts={facts ?? []} />}
  </article>
}

export function SpecificationResult({ value }: { value: Result }) {
  const matches = value.matches ?? []
  const incomplete = value.incomplete ?? []
  const searching = value.matches !== undefined
  return <section className={styles.results} aria-label={searching ? 'Specification search results' : 'Electrical specifications'}>
    {searching && <div className={styles.overview}><strong>{value.match_count ?? matches.length} confirmed match{(value.match_count ?? matches.length) === 1 ? '' : 'es'}</strong>
      {(value.incomplete_count ?? incomplete.length) > 0 && <span> · {value.incomplete_count ?? incomplete.length} {(value.incomplete_count ?? incomplete.length) === 1 ? 'needs' : 'need'} evidence or conditions</span>}
    </div>}
    {matches.map(match => <SpecificationMatch key={match.part.id} part={match.part} facts={match.supporting_facts} />)}
    {incomplete.map(item => <SpecificationMatch key={item.part.id} part={item.part} missing={item.missing_or_unqualified} />)}
    {searching && !matches.length && !incomplete.length && <p className={styles.note}>No inventory records meet these requirements.</p>}
    {value.truncated && <p className={styles.note}>Showing {matches.length} confirmed and {incomplete.length} incomplete records. Narrow your search to see other candidates.</p>}
    {value.facts && <section className={styles.card}><strong>Accepted electrical facts{value.part_id ? ` · Part #${value.part_id}` : ''}</strong><SpecificationFacts facts={value.facts} /></section>}
    {value.pending_review && <div><p className={styles.overview}>Pending specification review</p><SpecificationReview review={value.pending_review} partId={value.part_id ?? 'unknown'} /></div>}
  </section>
}
