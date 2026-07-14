/**
 * D-14 审校收件箱：合并 DrawingAnnotationQueue 的「符号标注」候选
 * 与 SemanticReviewQueue 的「成果审校（拓扑/命名/规范）」候选为一个列表。
 *
 * 纯函数/类型层，不含组件逻辑，便于单测（见 __tests__/reviewInbox.test.ts）。
 * 优先级公式与后端 routers/model_review.py `_priority()` 保持一致：
 *   priority = (冲突 ? 1000 : 0) + 100 * (1 - confidence)
 * 未知置信度按 0.5（中性不确定）处理，与后端 `_UNKNOWN_CONFIDENCE` 对齐。
 */
import type { CandidateSource, ReviewTargetKind, SymbolAnnotation, SymbolCategory } from '@/services/modelReview'

export type ReviewInboxKind = 'symbol' | 'semantic'

/** 成果审校队列原始行（对齐后端 model_review.build_review_queue 输出契约）。 */
export interface SemanticQueueRow {
  id: string
  target_kind: ReviewTargetKind
  title: string
  detail?: string
  confidence?: number | null
  source?: CandidateSource | null
  conflict?: boolean
  category?: string | null
  suggested_category?: string | null
  discipline?: string | null
  mep_system?: string | null
  drawing_id?: string | null
  priority?: number
}

export interface SemanticQueueSummary {
  total: number
  conflict_count: number
  low_confidence_count: number
  by_kind: Record<string, number>
}

export interface SymbolDrawingOption {
  drawingId: string
  title: string
  thumbnailUrl?: string
}

/** 统一收件箱行：symbol/semantic 两类候选归一化为同一展示+排序契约。 */
export interface ReviewInboxItem {
  key: string
  kind: ReviewInboxKind
  title: string
  detail?: string
  confidence: number | null
  conflict: boolean
  priority: number
  source?: CandidateSource | string | null
  category?: string | null
  suggestedCategory?: string | null
  discipline?: string | null
  drawingId?: string | null
  /** kind='semantic' 时用于回传原 target_kind（topology/naming/compliance/element/symbol）。 */
  targetKind?: ReviewTargetKind
  raw: SemanticQueueRow | SymbolAnnotation
}

const UNKNOWN_CONFIDENCE = 0.5
const CONFLICT_WEIGHT = 1000

/** 与后端 `_priority()` 一字不差的客户端镜像，保证前后端排序口径一致。 */
export function computePriority(conflict: boolean, confidence?: number | null): number {
  const base = conflict ? CONFLICT_WEIGHT : 0
  const conf = confidence ?? UNKNOWN_CONFIDENCE
  return Math.round((base + 100 * (1 - conf)) * 10000) / 10000
}

export function fromSemanticQueueRow(row: SemanticQueueRow): ReviewInboxItem {
  const conflict = Boolean(row.conflict)
  return {
    key: `semantic:${row.target_kind}:${row.id}`,
    kind: 'semantic',
    title: row.title,
    detail: row.detail,
    confidence: row.confidence ?? null,
    conflict,
    priority: row.priority ?? computePriority(conflict, row.confidence),
    source: row.source,
    category: row.category,
    suggestedCategory: row.suggested_category,
    discipline: row.discipline,
    drawingId: row.drawing_id,
    targetKind: row.target_kind,
    raw: row,
  }
}

export function fromSymbolAnnotation(drawingId: string, annotation: SymbolAnnotation): ReviewInboxItem {
  const conflict = false
  return {
    key: `symbol:${annotation.id ?? `${drawingId}:${annotation.bbox.join(',')}`}`,
    kind: 'symbol',
    title: `${CATEGORY_LABEL[String(annotation.category)] ?? annotation.category}`,
    detail: STATUS_LABEL[annotation.status] ?? annotation.status,
    confidence: annotation.confidence ?? null,
    conflict,
    priority: computePriority(conflict, annotation.confidence),
    source: annotation.source,
    category: String(annotation.category),
    drawingId,
    raw: annotation,
  }
}

/** 冲突优先 → 同层低置信优先 → key 稳定序（与后端排序口径一致）。 */
export function sortInboxItems(items: ReviewInboxItem[]): ReviewInboxItem[] {
  return [...items].sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority
    return a.key.localeCompare(b.key)
  })
}

// ── 9 类 taxonomy（对齐后端 layer_conventions / 迁移 024，symbol 与 semantic 共用）──

export const SYMBOL_CATEGORIES: SymbolCategory[] = [
  'column', 'beam', 'slab', 'wall', 'door', 'window', 'pipe', 'equipment', 'axis',
]

export const CATEGORY_LABEL: Record<string, string> = {
  column: '柱', beam: '梁', slab: '板', wall: '墙', door: '门',
  window: '窗', pipe: '管线', equipment: '设备', axis: '轴网',
}

export const CATEGORY_OPTIONS = SYMBOL_CATEGORIES.map((value) => ({
  value,
  label: `${CATEGORY_LABEL[value]}（${value}）`,
}))

export const STATUS_LABEL: Record<string, string> = {
  pending: '待审', confirmed: '已确认', rejected: '已否定', reclassed: '已改类',
}

export const TARGET_KIND_LABEL: Record<ReviewTargetKind, { label: string; color: string }> = {
  topology: { label: '拓扑闭合', color: 'geekblue' },
  naming: { label: '构件命名', color: 'purple' },
  compliance: { label: '规范符合性', color: 'volcano' },
  element: { label: '构件', color: 'blue' },
  symbol: { label: '符号', color: 'cyan' },
}

/** 置信度着色：红(低,优先审)→黄(中)→绿(高)，与 services/modelReview.ts confidenceColor 一致。 */
export function confidenceColor(confidence?: number | null): string {
  if (confidence == null) return '#8c8c8c'
  if (confidence < 0.5) return '#ff4d4f'
  if (confidence < 0.75) return '#faad14'
  return '#52c41a'
}
