import styles from './FieldReviewEditor.module.css'
import { DISPLAY_PART_FIELDS, type Part, type PendingReview } from './types'

interface FieldReviewEditorProps {
  part: Part
  review: PendingReview
  colSpan: number
  saving: boolean
  onToggleField: (key: string) => void
  onEditField: (key: string, value: string) => void
  onSave: () => void
  onDismiss: () => void
}

export function FieldReviewEditor({
  part,
  review,
  colSpan,
  saving,
  onToggleField,
  onEditField,
  onSave,
  onDismiss,
}: FieldReviewEditorProps) {
  const acceptedCount = Object.values(review.fields).filter(field => field.accepted).length
  const provenanceByField = Object.fromEntries(
    review.provenance.map(entry => [entry.field_name, entry]),
  )

  return (
    <tr className={styles.reviewRow}>
      <td colSpan={colSpan} className={styles.reviewCell}>
        <div className={styles.reviewHeader}>
          <span className={styles.reviewTitle}>Pending Review</span>
          <span className={styles.reviewOutcome}>{review.outcome}</span>
        </div>
        <table className={styles.reviewTable}>
          <tbody>
            {DISPLAY_PART_FIELDS.filter(field => field.key in review.fields).map(field => {
              const reviewField = review.fields[field.key]
              const provenance = provenanceByField[field.key]
              return (
                <tr
                  key={field.key}
                  className={reviewField.accepted ? styles.reviewFieldAccepted : styles.reviewFieldRejected}
                >
                  <td className={styles.rfCheck}>
                    <input
                      type="checkbox"
                      checked={reviewField.accepted}
                      onChange={() => onToggleField(field.key)}
                    />
                  </td>
                  <td className={styles.rfLabel}>{field.label}</td>
                  <td className={styles.rfOld}>{String(part[field.key] ?? '—')}</td>
                  <td className={styles.rfArrow}>→</td>
                  <td className={styles.rfNew}>
                    <input
                      className={styles.rfInput}
                      value={reviewField.value}
                      disabled={!reviewField.accepted}
                      onChange={event => onEditField(field.key, event.target.value)}
                    />
                    {provenance && (
                      <div className={styles.rfMeta}>
                        <div className={styles.rfSourceLine}>
                          <span>{provenance.source_tier ?? 'unknown-tier'}</span>
                          <span>{provenance.source_kind ?? 'unknown-source'}</span>
                          <span>{provenance.extraction_method ?? 'unknown-method'}</span>
                          {provenance.confidence_marker && <span>{provenance.confidence_marker}</span>}
                        </div>
                        {provenance.source_locator && (
                          <div className={styles.rfLocator} title={provenance.source_locator}>
                            {provenance.source_locator}
                          </div>
                        )}
                        {provenance.evidence && (
                          <div className={styles.rfEvidence}>
                            {provenance.evidence}
                          </div>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div className={styles.reviewActions}>
          <button
            className={styles.acceptBtn}
            onClick={onSave}
            disabled={saving || acceptedCount === 0}
          >
            {saving ? '…' : `Save ${acceptedCount} field${acceptedCount !== 1 ? 's' : ''}`}
          </button>
          <button className={styles.dismissBtn} onClick={onDismiss}>
            Dismiss
          </button>
        </div>
      </td>
    </tr>
  )
}
