export interface Part {
  id?: number
  part_category: string
  profile: string
  value: string | null
  package: string | null
  part_number: string | null
  quantity: number
  manufacturer: string | null
  description: string | null
}

export interface FieldReview {
  accepted: boolean
  value: string
}

export interface PendingReviewProvenance {
  field_name: string
  field_value?: string | null
  source_tier?: string | null
  source_kind?: string | null
  source_locator?: string | null
  extraction_method?: string | null
  confidence_marker?: string | null
  conflict_status?: string | null
  normalization_method?: string | null
  evidence?: string | null
}

export interface FieldProvenance extends PendingReviewProvenance {
  competing_candidates?: string | null
}

export interface PendingReview {
  fields: Record<string, FieldReview>
  provenance: PendingReviewProvenance[]
  outcome: string
}

export const DISPLAY_PART_FIELDS: { key: keyof Part; label: string }[] = [
  { key: 'part_category', label: 'Category' },
  { key: 'value', label: 'Value' },
  { key: 'package', label: 'Package' },
  { key: 'quantity', label: 'Qty' },
  { key: 'part_number', label: 'Part #' },
  { key: 'manufacturer', label: 'Manufacturer' },
  { key: 'description', label: 'Description' },
]

export type MessageRole = 'user' | 'system'

export interface UserMessage {
  role: 'user'
  text: string
  photoUrl?: string // object URL for thumbnail display
}

export interface SystemMessage {
  role: 'system'
  kind:
    | 'chat'
    | 'text'
    | 'ingest-result'
    | 'ingest-increment'
    | 'clarification'
    | 'query-result'
    | 'not-found'
    | 'error'
  text?: string
  part?: Part
  matches?: Part[]
}

export type Message = UserMessage | SystemMessage
