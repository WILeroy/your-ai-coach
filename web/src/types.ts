export interface ViewSpecBase { view: string; title?: string }

export interface LineViewSpec extends ViewSpecBase {
  view: 'line'
  x: (string | number)[]
  series: { name: string; data: (number | null)[] }[]
  y_name?: string
  connect_nulls?: boolean
  mark_line?: number
}

export interface BarViewSpec extends ViewSpecBase {
  view: 'bar'
  x: string[]
  series: { name: string; data: number[] }[]
  y_name?: string
  stack?: boolean
}

export interface TableViewSpec extends ViewSpecBase {
  view: 'table'
  columns: { key: string; label: string }[]
  rows: Record<string, unknown>[]
}

export interface MetricCardsSpec extends ViewSpecBase {
  view: 'metric_cards'
  cards: { label: string; value: string | number; unit?: string; sub?: string }[]
}

export interface NavigateSpec { view: "navigate"; page: string; title?: string }
export interface RefreshSpec { view: "refresh"; title?: string }

export type ViewSpec = LineViewSpec | BarViewSpec | TableViewSpec | MetricCardsSpec | NavigateSpec | RefreshSpec | (ViewSpecBase & Record<string, unknown>)

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  toolTags?: { name: string; error?: boolean }[]
  confirm?: PendingConfirm | null
  streaming?: boolean
}

export interface PendingConfirm {
  action_id: string
  tool: string
  preview: Record<string, unknown>
  actions?: { tool: string; preview: Record<string, unknown> }[]
  done?: boolean
}

export interface ChatSessionInfo {
  session_id: string
  title: string
  last_at: string
  msg_count: number
}
