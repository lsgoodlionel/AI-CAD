import { request } from '@umijs/max'

const BASE = '/api/v1'

// ── 图纸 CRUD ────────────────────────────────────────────────

export const listDrawings = (params?: {
  project_id?: string
  discipline?: string
  status?: string
  limit?: number
  offset?: number
}) => request(`${BASE}/drawings`, { params })

export const getDrawing = (id: string) =>
  request(`${BASE}/drawings/${id}`)

export const getDownloadUrl = (id: string) =>
  request(`${BASE}/drawings/${id}/download-url`)

export const uploadDrawing = (formData: FormData) =>
  request(`${BASE}/drawings`, {
    method: 'POST',
    data: formData,
    requestType: 'form',
  })

// ── 一审 ────────────────────────────────────────────────────

export const submitTechnicalReview = (
  drawingId: string,
  data: {
    result: 'approved' | 'rejected'
    ai_report_confirmed?: boolean
    bim_check_confirmed?: boolean
    issues_all_closed?: boolean
    notes?: string
  },
) =>
  request(`${BASE}/drawings/${drawingId}/technical-review`, {
    method: 'POST',
    data,
  })

export const startTechnicalReview = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/technical-review/start`, {
    method: 'POST',
  })

// ── 二审 ────────────────────────────────────────────────────

export const getEconomicReview = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/economic-review`)

export const submitEconomicAlternatives = (
  drawingId: string,
  data: {
    alternatives: { option_id: string; description: string; cost_est: number; notes?: string }[]
    selected_option?: string
    total_saving_est?: number
    notes?: string
  },
) =>
  request(`${BASE}/drawings/${drawingId}/economic-review`, {
    method: 'POST',
    data,
  })

export const signEconomicReview = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/economic-review/sign`, {
    method: 'POST',
    data: { confirm: true },
  })

export const approveEconomicReview = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/economic-review/approve`, {
    method: 'POST',
  })

export const rejectEconomicReview = (drawingId: string, notes: string) =>
  request(`${BASE}/drawings/${drawingId}/economic-review/reject`, {
    method: 'POST',
    params: { notes },
  })

// ── 三审 ────────────────────────────────────────────────────

export const getSettlementReview = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/settlement-review`)

export const submitSettlementNodes = (
  drawingId: string,
  data: {
    settlement_nodes: { node_name: string; description: string; amount?: number }[]
    notes?: string
  },
) =>
  request(`${BASE}/drawings/${drawingId}/settlement-review`, {
    method: 'POST',
    data,
  })

export const uploadQuotaSheet = (drawingId: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request(`${BASE}/drawings/${drawingId}/settlement-review/quota`, {
    method: 'POST',
    data: fd,
    requestType: 'form',
  })
}

export const approveSettlementReview = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/settlement-review/approve`, {
    method: 'POST',
  })

export const rejectSettlementReview = (drawingId: string, notes: string) =>
  request(`${BASE}/drawings/${drawingId}/settlement-review/reject`, {
    method: 'POST',
    params: { notes },
  })

// ── AI 审查报告 ──────────────────────────────────────────────

export const getAiReviewIssues = (
  drawingId: string,
  params?: { severity?: string; status?: string; limit?: number; offset?: number },
) => request(`${BASE}/drawings/${drawingId}/ai-review/issues`, { params })

export const getAiReviewProgress = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/ai-review/progress`)

export const retryAiReview = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/ai-review/retry`, {
    method: 'POST',
  })

export const getAiReviewReportPdfUrl = (drawingId: string) =>
  `${BASE}/drawings/${drawingId}/ai-review/report-pdf`

export const getAiReviewReportExcelUrl = (drawingId: string) =>
  `${BASE}/drawings/${drawingId}/ai-review/report-excel`

// ── 经济测算（钢筋翻样）───────────────────────────────────────

export type BarItemInput = {
  diameter: number
  steel_grade: string
  required_length: number
  count: number
}

export type EconomicCalcRequest = {
  concrete_grade: string
  seismic_grade: number
  steel_price_per_ton: number
  bars: BarItemInput[]
}

export const runEconomicCalc = (drawingId: string, data: EconomicCalcRequest) =>
  request(`${BASE}/drawings/${drawingId}/economic-calc`, { method: 'POST', data })

export const getEconomicCalc = (drawingId: string) =>
  request(`${BASE}/drawings/${drawingId}/economic-calc`)

// ── 批量上传（蓝图 4.1）──────────────────────────────────────

export interface BatchUploadCreatedItem {
  drawing_id: string
  drawing_no: string
  filename: string
}

export interface BatchUploadFailedItem {
  filename: string
  error: string
}

export interface BatchUploadResult {
  created: BatchUploadCreatedItem[]
  failed: BatchUploadFailedItem[]
  review_triggered: number
}

export interface ZipImportResult extends BatchUploadResult {
  skipped: string[]
}

/** 多文件批量上传：fd 含 project_id / items_meta(JSON) / files / auto_review */
export const batchUploadDrawings = (fd: FormData) =>
  request<BatchUploadResult>(`${BASE}/drawings/batch`, {
    method: 'POST',
    data: fd,
    requestType: 'form',
  })

/** ZIP 整套导入：fd 含 project_id / file(zip) / auto_review */
export const importDrawingsZip = (fd: FormData) =>
  request<ZipImportResult>(`${BASE}/drawings/import-zip`, {
    method: 'POST',
    data: fd,
    requestType: 'form',
  })

// ── 套图审查（蓝图 4.4）──────────────────────────────────────

export type ReviewBatchScope = 'single' | 'multi' | 'full_set'

export type ReviewBatchStatus =
  | 'pending'
  | 'processing'
  | 'done'
  | 'partial_failed'
  | 'failed'

export interface ReviewBatchSummary {
  total: number
  done: number
  failed: number
  issues_total: number
  critical_total: number
  by_severity: Record<string, number>
  by_discipline: Record<string, number>
}

/** cross_drawing.analyze_batch 输出（蓝图 4.6，中文键名） */
export interface CrossDrawingFindings {
  重复图号?: { drawing_no: string; drawing_ids: string[] }[]
  版本冲突?: { drawing_no: string; versions: string[] }[]
  接口缺图?: {
    missing_discipline: string
    referenced_by: { drawing_no: string; interface: string }[]
  }[]
  问题聚类?: {
    location_key: string
    count: number
    drawings: string[]
    disciplines: string[]
  }[]
  高频对象聚合?: { name: string; count: number }[]
  严重度分布?: Record<string, number>
  专业分布?: Record<string, number>
}

/** review_batches 行（JSONB 字段经 asyncpg 可能以 JSON 文本返回） */
export interface ReviewBatch {
  id: string
  project_id: string
  scope: ReviewBatchScope
  drawing_ids: string[] | string
  status: ReviewBatchStatus
  summary?: ReviewBatchSummary | string | null
  cross_findings?: CrossDrawingFindings | string | null
  created_by?: string | null
  created_at: string
  completed_at?: string | null
}

export interface ReviewBatchListResult {
  items: ReviewBatch[]
  total: number
}

export interface ReviewBatchDrawingItem {
  drawing_id: string
  drawing_no: string
  title: string
  discipline: string
  report_status: string
  total_issues: number
  critical_issues: number
}

export interface ReviewBatchProgress {
  total: number
  done: number
  failed: number
  processing: number
}

export interface ReviewBatchDetail {
  batch: ReviewBatch
  items: ReviewBatchDrawingItem[]
  progress: ReviewBatchProgress
}

export interface CreateReviewBatchBody {
  project_id: string
  drawing_ids?: string[]
  scope?: ReviewBatchScope
}

export interface CreateReviewBatchResult {
  batch_id: string
  scope: ReviewBatchScope
  total: number
  triggered: number
}

export const createReviewBatch = (body: CreateReviewBatchBody) =>
  request<CreateReviewBatchResult>(`${BASE}/review-batches`, {
    method: 'POST',
    data: body,
  })

export const listReviewBatches = (params?: {
  project_id?: string
  limit?: number
  offset?: number
}) => request<ReviewBatchListResult>(`${BASE}/review-batches`, { params })

export const getReviewBatch = (id: string) =>
  request<ReviewBatchDetail>(`${BASE}/review-batches/${id}`)

// ── 套图审查展示辅助（供列表/详情页共用，参考 services/reviewAudit.ts 先例）──

export type BatchBadgeStatus = 'default' | 'processing' | 'success' | 'warning' | 'error'

export const REVIEW_BATCH_STATUS_META: Record<
  ReviewBatchStatus,
  { badge: BatchBadgeStatus; text: string }
> = {
  pending: { badge: 'default', text: '等待中' },
  processing: { badge: 'processing', text: '审查中' },
  done: { badge: 'success', text: '已完成' },
  partial_failed: { badge: 'warning', text: '部分失败' },
  failed: { badge: 'error', text: '失败' },
}

export const REVIEW_BATCH_SCOPE_META: Record<ReviewBatchScope, { color: string; text: string }> = {
  single: { color: 'default', text: '单张' },
  multi: { color: 'blue', text: '多张' },
  full_set: { color: 'geekblue', text: '整套' },
}

export const REVIEW_BATCH_TERMINAL_STATUSES: ReviewBatchStatus[] = [
  'done',
  'partial_failed',
  'failed',
]

/** JSONB 字段经 asyncpg 可能以 JSON 文本返回，统一安全解析。 */
export function coerceJson<T>(value: unknown, fallback: T): T {
  if (value == null) return fallback
  if (typeof value === 'string') {
    try {
      return JSON.parse(value) as T
    } catch {
      return fallback
    }
  }
  return value as T
}
