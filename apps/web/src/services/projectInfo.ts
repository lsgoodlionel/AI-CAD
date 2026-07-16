/**
 * 工程信息模块 API(Phase E1)
 *
 * 后端:routers/project_info.py(drawing_extracted_info 聚合)
 *      routers/drawings.py GET /{id}/preview(统一预览)
 */
import { request } from '@umijs/max'

const BASE = '/api/v1/projects'

// ── 类型 ─────────────────────────────────────────────────────────

/** 与后端 category 词汇对齐(core/model3d/ocr TokenKind + 扩展) */
export type InfoCategory =
  | 'elevation'
  | 'axis'
  | 'dimension'
  | 'level_name'
  | 'room_name'
  | 'note'
  | 'title'
  | 'title_block'
  | 'design_note'
  | 'other'

export const INFO_CATEGORY_LABEL: Record<string, string> = {
  elevation: '标高',
  axis: '轴线',
  dimension: '尺寸标注',
  level_name: '楼层名',
  room_name: '房间/空间',
  note: '文字说明',
  title: '图名/标题',
  title_block: '图签信息',
  design_note: '设计说明',
  other: '其他',
}

export const INFO_EXTRACTOR_LABEL: Record<string, string> = {
  vector_text: '矢量文字',
  ocr: 'OCR 识别',
  grid_anchor: '轴网识别',
  section_level: '剖面标高',
  filename: '文件名解析',
  vlm: '大模型读图',
}

export interface InfoItem {
  id: string
  drawing_id: string
  category: string
  content: string
  value_json: Record<string, unknown> | null
  location_json: Record<string, unknown> | null
  extractor: string
  confidence: number | null
  extraction_version: number
  source_kind?: 'auto' | 'verified'
  drawing_no: string
  drawing_title: string
  discipline: string
}

/** 人审修正入参:content 必填;标高/尺寸类附解析值 */
export interface VerifyPayload {
  category: string
  content: string
  value_json?: Record<string, unknown> | null
  supersedes_id?: string | null
}

export interface InfoSummary {
  categories: { category: string; count: number }[]
  coverage: { total_drawings: number; extracted_drawings: number }
}

export interface InfoItemsResult {
  total: number
  page: number
  page_size: number
  items: InfoItem[]
}

export interface DrawingPreview {
  kind: 'pdf' | 'image'
  url: string
}

// ── API ──────────────────────────────────────────────────────────

export const getInfoSummary = (projectId: string): Promise<InfoSummary> =>
  request(`${BASE}/${projectId}/info/summary`)

export const listInfoItems = (
  projectId: string,
  params: {
    category?: string
    extractor?: string
    discipline?: string
    q?: string
    page?: number
    page_size?: number
  },
): Promise<InfoItemsResult> =>
  request(`${BASE}/${projectId}/info/items`, { params })

export const getInfoAxes = (projectId: string): Promise<{ axes: InfoItem[] }> =>
  request(`${BASE}/${projectId}/info/axes`)

export const triggerInfoExtract = (
  projectId: string,
): Promise<{ task_id: string; project_id: string }> =>
  request(`${BASE}/${projectId}/info/extract`, { method: 'POST' })

/** 统一预览:PDF/图片原文件,DXF/DWG 走服务端渲染 PNG;422 = 暂不支持 */
export const getDrawingPreview = (drawingId: string): Promise<DrawingPreview> =>
  request(`/api/v1/drawings/${drawingId}/preview`, { skipErrorHandler: true })

/** 人审修正:写 verified 行(生效值),触发建模增量重建 */
export const verifyArchiveItem = (
  drawingId: string,
  payload: VerifyPayload,
): Promise<{ ok: boolean }> =>
  request(`/api/v1/drawings/${drawingId}/archive/verify`, {
    method: 'POST',
    data: payload,
  })
