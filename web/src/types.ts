export interface ViewSpecBase {
  view: string
  title?: string
  window?: string
  confidence?: string
  generated_at?: string
  note?: string
}

export interface LineViewSpec extends ViewSpecBase {
  view: 'line'
  x: (string | number)[]
  series: { name: string; data: (number | null)[] }[]
  y_name?: string
  connect_nulls?: boolean
  mark_line?: number
  mark_area?: [number, number]
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

export interface InsightViewSpec extends ViewSpecBase {
  view: 'insight'
  headline: string
  status?: 'ready' | 'caution' | 'limited' | string
  status_label?: string
  cards: { label: string; value: string | number; unit?: string; sub?: string; reference?: string }[]
  evidence?: { label: string; value: string; detail: string }[]
  caveats?: string[]
  readiness_signals?: string[]
  generated_at?: string
  window?: string
  confidence?: 'high' | 'medium' | 'low' | string
}

export interface PanelsViewSpec extends ViewSpecBase {
  view: 'panels'
  x: (string | number)[]
  panels: { name: string; unit?: string; data: (number | null)[]; reference?: string }[]
  note?: string
  window?: string
  confidence?: string
}

export interface SessionListItem {
  id: number
  date: string
  type: string
  status: string
  status_label: string
  tone: string
  record_state: string
  planned: string[]
  actual: string[]
  planned_notes: string
  actual_notes: string
  rpe: number | null
}

export interface SessionListViewSpec extends ViewSpecBase {
  view: 'session_list'
  sessions: SessionListItem[]
}

export interface NavigateSpec extends ViewSpecBase { view: "navigate"; page: string }
export interface RefreshSpec extends ViewSpecBase { view: "refresh" }

export type ViewSpec = LineViewSpec | BarViewSpec | TableViewSpec | MetricCardsSpec | InsightViewSpec | PanelsViewSpec | SessionListViewSpec | NavigateSpec | RefreshSpec | (ViewSpecBase & Record<string, unknown>)

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
