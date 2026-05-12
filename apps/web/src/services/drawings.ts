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

export const getAiReviewReportPdfUrl = (drawingId: string) =>
  `${BASE}/drawings/${drawingId}/ai-review/report-pdf`

export const getAiReviewReportExcelUrl = (drawingId: string) =>
  `${BASE}/drawings/${drawingId}/ai-review/report-excel`
