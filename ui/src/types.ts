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

export type MessageRole = 'user' | 'system'

export interface UserMessage {
  role: 'user'
  text: string
  photoUrl?: string // object URL for thumbnail display
}

export interface SystemMessage {
  role: 'system'
  kind:
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
