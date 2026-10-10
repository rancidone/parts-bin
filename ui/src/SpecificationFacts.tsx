import type { Part } from './types'
import { PartCard } from './PartCard'

type Fact = { name: string; value: string; basis: string; conditions: Record<string, string>; evidence: { kind: string; excerpt: string; url?: string; page?: number } }

export function SpecificationFacts({ facts }: { facts: Fact[] }) {
  return <dl>{facts.map(fact => <div key={fact.name}>
    <dt><strong>{fact.name.replaceAll('_', ' ')}</strong>: {fact.value} ({fact.basis.replaceAll('_', ' ')})</dt>
    <dd>{Object.entries(fact.conditions).map(([key, value]) => `${key}: ${value}`).join('; ') || 'No conditions stated'}<br />
      {fact.evidence.kind === 'source' ? <><a href={fact.evidence.url} target="_blank" rel="noreferrer">Source</a>, page {fact.evidence.page}</> : 'User assertion'}<br />
      <q>{fact.evidence.excerpt}</q></dd>
  </div>)}</dl>
}

type Review = { facts: Fact[]; snapshot: { metadata: { part_number: string | null; package: string | null; part_category: string } } }

export function SpecificationReview({ review, partId }: { review: Review; partId: string | number }) {
  const identity = review.snapshot.metadata
  return <div><strong>Part #{partId}: {identity.part_number ?? identity.part_category}</strong>
    <div>{identity.package ?? 'Package unknown'}</div><SpecificationFacts facts={review.facts} /></div>
}

export function SpecificationResult({ value }: { value: { matches?: { part: Part; supporting_facts: Fact[] }[]; incomplete?: { part: Part; missing_or_unqualified: string[] }[]; facts?: Fact[]; part_id?: number; pending_review?: Review | null; truncated?: boolean } }) {
  return <div>
    {value.matches?.map(match => <div key={match.part.id}><strong>Requirements matched — Part #{match.part.id}: {match.part.part_number ?? 'Ordering code unknown'}</strong><PartCard part={match.part} /><SpecificationFacts facts={match.supporting_facts} /></div>)}
    {value.matches?.length === 0 && <div>No confirmed matches.</div>}
    {value.incomplete?.map(item => <div key={item.part.id}><strong>Needs evidence or conditions — Part #{item.part.id}: {item.part.part_number ?? 'Ordering code unknown'}</strong><PartCard part={item.part} /><div>{item.missing_or_unqualified.join(', ')}</div></div>)}
    {value.truncated && <div>More candidates exist than shown.</div>}
    {value.facts && <><strong>Accepted facts</strong><SpecificationFacts facts={value.facts} /></>}
    {value.pending_review && <><strong>Pending specification review</strong><SpecificationReview review={value.pending_review} partId={value.part_id ?? 'unknown'} /></>}
  </div>
}
