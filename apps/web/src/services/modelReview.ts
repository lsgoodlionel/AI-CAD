/**
 * Phase C 泳道 D 前端审校共享契约（C-15 / C-16 / C-17 公共缝）。
 *
 * 对齐后端迁移 024_review_actions.sql 的两张表：
 * - model_review_actions（人审动作埋点，C-15/C-16 写、C-17 读）
 * - model_symbol_annotations（符号级标注，C-16 写）
 * 类别锚定 9 类 taxonomy（column/beam/slab/wall/door/window/pipe/equipment/axis）。
 */
import { request } from '@umijs/max'

const BASE = '/api/v1'

// ── 类型契约 ────────────────────────────────────────────────

export type SymbolCategory =
  | 'column' | 'beam' | 'slab' | 'wall'
  | 'door' | 'window' | 'pipe' | 'equipment' | 'axis'

export type MepSystem = '消防' | '给排水' | '电气' | '暖通'

/** 审校对象类别 */
export type ReviewTargetKind =
  | 'symbol' | 'element' | 'topology' | 'naming' | 'compliance'

/** 人审动作类型 */
export type ReviewActionType =
  | 'confirm' | 'reject' | 'reclass' | 'addbox' | 'edit'

/** 候选/标注来源 */
export type CandidateSource = 'rule' | 'model' | 'fused' | 'human'

/** 符号级标注（候选框 + 置信度 + 金标签状态） */
export interface SymbolAnnotation {
  id?: number
  projectId: string
  drawingId: string
  category: SymbolCategory | string
  mepSystem?: MepSystem | null
  bbox: [number, number, number, number] // [x_min, y_min, x_max, y_max]
  confidence?: number
  source: CandidateSource
  status: 'pending' | 'confirmed' | 'rejected' | 'reclassed'
  primitiveIds?: number[]
  reviewerId?: string
  evidence?: Record<string, unknown>
}

/** 人审动作埋点记录 */
export interface ReviewAction {
  projectId: string
  drawingId?: string
  targetKind: ReviewTargetKind
  targetId?: string
  actionType: ReviewActionType
  oldCategory?: string
  newCategory?: string
  mepSystem?: string
  discipline?: string
  source?: CandidateSource
  confidence?: number
  note?: string
}

/** C-17 返工收敛度量 */
export interface ReviewMetrics {
  confirmRate: number
  reclassRate: number
  rejectRate: number
  addboxRate: number
  byDiscipline: Record<string, { total: number; rework: number; reworkRate: number }>
  byCategory: Record<string, { total: number; rework: number; reworkRate: number }>
  trend: Array<{ period: string; reworkRate: number; count: number }>
}

// ── 置信度着色（低置信优先审，红→黄→绿）───────────────────────

export const confidenceColor = (c?: number): string => {
  if (c == null) return '#8c8c8c'
  if (c < 0.5) return '#ff4d4f' // 低置信：红，优先审
  if (c < 0.75) return '#faad14' // 中置信：黄
  return '#52c41a' // 高置信：绿
}

// ── API 端点契约（后端 C-16 model_annotations / C-15 model_review / C-17 dashboard）──

/** C-16：拉取某图纸符号标注（含模型候选 + 人审状态） */
export const listSymbolAnnotations = (projectId: string, drawingId: string) =>
  request<{ success: boolean; data: SymbolAnnotation[] }>(
    `${BASE}/projects/${projectId}/drawings/${drawingId}/symbol-annotations`,
  )

/** C-16：保存/确认/否定/改类/补框（后端同时写 review_actions 埋点） */
export const saveSymbolAnnotation = (
  projectId: string,
  drawingId: string,
  body: Partial<SymbolAnnotation> & { actionType: ReviewActionType },
) =>
  request(`${BASE}/projects/${projectId}/drawings/${drawingId}/symbol-annotations`, {
    method: 'POST',
    data: body,
  })

/** C-16：导出标注（COCO-like/自定义，喂 C-09 训练） */
export const exportAnnotations = (projectId: string, params?: { format?: string }) =>
  request(`${BASE}/projects/${projectId}/symbol-annotations/export`, { params })

/** C-15：语义审校队列（低置信/冲突优先） */
export const getReviewQueue = (projectId: string) =>
  request(`${BASE}/projects/${projectId}/model/review-queue`)

/** C-15：提交语义审校动作（拓扑/命名/规范，后端写 review_actions + audit_logs） */
export const submitReviewAction = (projectId: string, body: ReviewAction) =>
  request(`${BASE}/projects/${projectId}/model/review-actions`, {
    method: 'POST',
    data: body,
  })

/** C-17：返工收敛度量看板数据 */
export const getReviewMetrics = (params: {
  project_id?: string
  discipline?: string
}) =>
  request<{ success: boolean; data: ReviewMetrics }>(
    `${BASE}/dashboard/model-review-metrics`,
    { params },
  )
