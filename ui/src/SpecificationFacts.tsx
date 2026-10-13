import type { Part } from './types'
import styles from './SpecificationFacts.module.css'

type Fact = { name: string; value: string; basis: string; conditions: Record<string, string>; evidence: { kind: string; excerpt: string; url?: string; page?: number; supporting_passages?: { page: number; excerpt: string }[] } }
type FailedRequirement = { requirement: { name: string; comparison: string; value: string; basis: string; conditions: Record<string, string> }; fact: Fact }
type FilterMismatch = { field: string; requested: string; actual: string; unit: string; relative_difference: string }
type PartialMatch = { part: Part; supporting_facts: Fact[]; failed_requirements: FailedRequirement[]; filter_mismatches: FilterMismatch[]; missing_or_unqualified: string[]; matched_requirement_count: number }
type Review = { facts: Fact[]; snapshot: { metadata: { part_number: string | null; package: string | null; part_category: string } } }
type Assessment = { confidence_score: number; rejected_fields?: { name: string; reason: string }[]; score_explanation: string; missing_fields: string[]; incomplete_fields?: Record<string, string[]>; reasons: string[]; relevant_pages: { page: number; reason: string; url: string }[] }
export type SpecificationState = { outcome?: string; extraction_assessment?: Assessment | null; clarification?: string | null; matches?: { part: Part; supporting_facts: Fact[] }[]; incomplete?: { part: Part; missing_or_unqualified: string[] }[]; facts?: Fact[]; part_id?: number; pending_review?: Review | null; truncated?: boolean; match_count?: number; incomplete_count?: number; partial_matches?: PartialMatch[]; partial_match_count?: number }

const label = (text: string) => text.replaceAll('_', ' ')

export function SpecificationFacts({ facts, reviewing = false }: { facts: Fact[]; reviewing?: boolean }) {
  if (!facts.length) return <p className={styles.note}>{reviewing ? 'No electrical specs to review.' : 'No electrical specs saved yet.'}</p>
  return <div className={styles.facts}>{facts.map(fact => <div className={styles.fact} key={fact.name}>
    <div className={styles.factHeading}><span>{label(fact.name)}</span><strong>{fact.value}</strong><span className={styles.basis}>{label(fact.basis)}</span></div>
    <div className={styles.conditions}>{Object.entries(fact.conditions).map(([key, value]) => `${label(key)}: ${value}`).join(' · ') || 'No conditions stated'}
      {fact.evidence.kind !== 'source' && <span className={styles.assertion}> · User assertion</span>}
    </div>
    <details className={styles.evidence} open={reviewing}>
      <summary>View {fact.evidence.kind === 'source' ? 'source evidence' : 'user assertion'}</summary>
      <div className={styles.evidenceBody}>
        {fact.evidence.kind === 'source' && <a href={`${fact.evidence.url}#page=${fact.evidence.page}`}  target="_blank" rel="noreferrer">Source document · page {fact.evidence.page}</a>}
        <blockquote>{fact.evidence.excerpt}</blockquote>
        {fact.evidence.kind === 'source' && fact.evidence.supporting_passages?.map((passage, index) => <div key={index}>
          <a href={`${fact.evidence.url}#page=${passage.page}`}  target="_blank" rel="noreferrer">Supporting source passage · page {passage.page}</a>
          <blockquote>{passage.excerpt}</blockquote>
        </div>)}
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

function SpecificationMatch({ part, facts, missing, partial }: { part: Part; facts?: Fact[]; missing?: string[]; partial?: PartialMatch }) {
  const incomplete = missing !== undefined && !partial
  const status = partial ? 'partial match' : incomplete ? 'needs evidence' : 'confirmed match'
  const comparison = (op: string) => ({ eq: 'equal to', gte: 'at least', lte: 'at most' })[op] ?? op
  return <article className={styles.card} aria-label={`Part ${part.id}: ${status}`}>
    <div className={styles.cardHeader}>
      <div className={styles.identity}><strong>{[part.part_category, part.value, part.package].filter(Boolean).join(' · ')}</strong>
        <span>{part.part_number ?? 'Ordering code unknown'} · <a href={`#/inventory/${part.id}`}>Part #{part.id}</a></span></div>
      <span className={styles.stock}>{part.quantity} in stock</span>
    </div>
    <span className={partial ? styles.partial : incomplete ? styles.incomplete : styles.confirmed}>{partial ? 'Partial match · does not meet all requirements' : incomplete ? 'Needs evidence or matching conditions' : 'Meets stated requirements'}</span>
    {partial && <>
      <p className={styles.note}>{partial.matched_requirement_count} electrical requirement{partial.matched_requirement_count === 1 ? '' : 's'} confirmed. Review the differences before choosing this part.</p>
      {partial.filter_mismatches.map(item => <p key={item.field} className={styles.mismatch}>Nominal {label(item.field)}: requested {item.requested}; stored {item.actual}.</p>)}
      {partial.failed_requirements.map((item, index) => <div className={styles.mismatch} key={index}>
        <strong>Does not meet: {label(item.requirement.name)} {comparison(item.requirement.comparison)} {item.requirement.value}.</strong>
        <SpecificationFacts facts={[item.fact]} />
      </div>)}
      {partial.missing_or_unqualified.length > 0 && <p className={styles.note}>Also not confirmed for: {partial.missing_or_unqualified.map(label).join(', ')}.</p>}
      {partial.supporting_facts.length > 0 && <details><summary>Requirements met</summary><SpecificationFacts facts={partial.supporting_facts} /></details>}
    </>}
    {incomplete ? <p className={styles.note}>Not confirmed for: {missing.map(label).join(', ')}.</p> : !partial && <SpecificationFacts facts={facts ?? []} />}
  </article>
}

export function SpecificationResult({ value }: { value: SpecificationState }) {
  const matches = value.matches ?? []
  const incomplete = value.incomplete ?? []
  const partial = value.partial_matches ?? []
  const searching = value.matches !== undefined
  return <section className={styles.results} aria-label={searching ? 'Specification search results' : 'Electrical specifications'}>
    {!value.extraction_assessment && value.clarification && <p className={styles.note}>{value.clarification}</p>}
    {value.extraction_assessment && <section className={styles.card} aria-label="PDF extraction assessment">
      <div className={styles.cardHeader}>
        <strong>{value.pending_review?.facts.length ? 'Specifications ready for review' : 'Datasheet extraction'}</strong>
        <span className={styles.coverage}>{Math.round(value.extraction_assessment.confidence_score * 100)}% coverage</span>
      </div>
      {value.clarification && <p className={styles.clarification}>{value.clarification}</p>}
      {value.extraction_assessment.missing_fields.length > 0 && <div className={styles.missing}>
        <strong>Not extracted</strong>
        <ul>{value.extraction_assessment.missing_fields.map(name => <li key={name}>{label(name)}</li>)}</ul>
      </div>}
      {value.extraction_assessment.relevant_pages.length > 0 && <details className={styles.diagnostics}>
        <summary>Open datasheet pages</summary>
        <ul className={styles.pageLinks}>{value.extraction_assessment.relevant_pages.map(page => <li key={page.page}>
          <a href={page.url} target="_blank" rel="noreferrer">Page {page.page}</a><span>{page.reason}</span>
        </li>)}</ul>
      </details>}
      <details className={styles.diagnostics}><summary>Extraction details</summary>
        <p className={styles.note}>{value.extraction_assessment.score_explanation}</p>
        {Object.entries(value.extraction_assessment.incomplete_fields ?? {}).map(([name, qualifiers]) => <p key={name}>{label(name)} needs: {qualifiers.map(label).join(', ')}.</p>)}
        {value.extraction_assessment.rejected_fields?.map(field => <p key={field.name}>{label(field.name)} was omitted: {field.reason}.</p>)}
        <ul>{value.extraction_assessment.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
      </details>
    </section>}
    {searching && <div className={styles.overview}><strong>{value.match_count ?? matches.length} confirmed match{(value.match_count ?? matches.length) === 1 ? '' : 'es'}</strong>
      {(value.incomplete_count ?? incomplete.length) > 0 && <span> · {value.incomplete_count ?? incomplete.length} {(value.incomplete_count ?? incomplete.length) === 1 ? 'needs' : 'need'} evidence or conditions</span>}
      {(value.partial_match_count ?? partial.length) > 0 && <span> · {value.partial_match_count ?? partial.length} partial match{(value.partial_match_count ?? partial.length) === 1 ? '' : 'es'}</span>}
    </div>}
    {matches.map(match => <SpecificationMatch key={match.part.id} part={match.part} facts={match.supporting_facts} />)}
    {partial.map(item => <SpecificationMatch key={item.part.id} part={item.part} partial={item} />)}
    {incomplete.map(item => <SpecificationMatch key={item.part.id} part={item.part} missing={item.missing_or_unqualified} />)}
    {searching && !matches.length && !incomplete.length && !partial.length && <p className={styles.note}>No inventory records meet these requirements.</p>}
    {value.truncated && <p className={styles.note}>Showing {matches.length} confirmed, {partial.length} partial and {incomplete.length} incomplete records. Narrow your search to see other candidates.</p>}
    {value.facts && <section className={styles.card}><strong>Electrical specs{value.part_id ? ` · Part #${value.part_id}` : ''}</strong><SpecificationFacts facts={value.facts} /></section>}
    {value.pending_review && <div><p className={styles.overview}>Pending specification review</p><SpecificationReview review={value.pending_review} partId={value.part_id ?? 'unknown'} /></div>}
  </section>
}
