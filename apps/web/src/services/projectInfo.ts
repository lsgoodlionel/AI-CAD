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
  withVlm = false,
): Promise<{ task_id: string; project_id: string; with_vlm: boolean }> =>
  request(`${BASE}/${projectId}/info/extract`, {
    method: 'POST',
    params: { with_vlm: withVlm },
  })

// ── 扫描进度(Phase F)──────────────────────────────────────────

export interface ScanSummary {
  total?: number
  by_category?: Record<string, number>
  by_extractor?: Record<string, number>
  samples?: { category: string; text: string; extractor: string }[]
  vlm_backend?: string
}

export interface ScanDrawing {
  drawing_id: string
  drawing_no: string
  title: string
  discipline: string
  status: 'pending' | 'extracting' | 'ready'
  item_count: number
  extractors_done: string[]
  summary: ScanSummary
  updated_at: string
}

export interface ScanProgress {
  overall: {
    total: number
    ready: number
    extracting: number
    pending: number
    percent: number
    /** G7:未处理(无 status 行,卡进度的隐形大头)/ 失败 / 已处理占比 */
    failed?: number
    unprocessed?: number
    processed_percent?: number
  }
  page: number
  drawings: ScanDrawing[]
}

export const getScanProgress = (
  projectId: string,
  params: { status?: string; page?: number; page_size?: number } = {},
): Promise<ScanProgress> =>
  request(`${BASE}/${projectId}/info/scan-progress`, { params })

/** 统一预览:PDF/图片原文件,DXF/DWG 走服务端渲染 PNG;422 = 暂不支持
 *
 * raster=true(标注模式):PDF 与 CAD 都渲成**等比** PNG,让三种格式统一走位图标注;
 * 等比是硬要求——「同除显示高度」的归一化坐标才对得上后端 page_h 口径。
 */
export const getDrawingPreview = (
  drawingId: string, opts: { raster?: boolean } = {},
): Promise<DrawingPreview> =>
  request(`/api/v1/drawings/${drawingId}/preview`, {
    params: opts.raster ? { raster: true } : undefined,
    skipErrorHandler: true,
  })

// ── 图纸追溯(Phase G:识别了什么 + 用在哪)────────────────────

export interface DrawingTrace {
  drawing: { id: string; drawing_no: string; title: string; discipline: string }
  info: {
    total: number
    by_category: Record<string, number>
    by_extractor: Record<string, number>
    /** 每类别实际识别内容样例(G5:具体什么内容) */
    samples?: Record<string, string[]>
  }
  model_usage: {
    used: boolean
    total_elements: number
    model_version?: number | null
    floors: { key: string; label: string; by_kind: Record<string, number>; count: number }[]
  }
}

export const getDrawingTrace = (drawingId: string): Promise<DrawingTrace> =>
  request(`/api/v1/drawings/${drawingId}/trace`)

export const ELEMENT_KIND_LABEL: Record<string, string> = {
  columns: '柱/桩',
  walls: '墙',
  beams: '梁',
  slabs: '板',
  pipes: '管线',
  equipment: '设备',
}

/** 人审修正:写 verified 行(生效值),触发建模增量重建 */
export const verifyArchiveItem = (
  drawingId: string,
  payload: VerifyPayload,
): Promise<{ ok: boolean }> =>
  request(`/api/v1/drawings/${drawingId}/archive/verify`, {
    method: 'POST',
    data: payload,
  })


// ── 比例尺确认(攻 drawing_transform 瓶颈,人审在环)──────────────

/** 图上写明的 `1:N` 比例尺候选;is_standard 者换算精确 */
export interface ScaleCandidate {
  denominator: number
  scale_m_pt: number
  votes: number
  share: number
  is_standard: boolean
  label: string
}

export interface ScaleCandidateItem {
  drawing_id: string
  drawing_no: string
  title: string
  discipline: string
  candidates: ScaleCandidate[]
  single: boolean
  /** 复核模式:现有变换的比例尺(非标准者疑似算错,如实测 1:2815 vs 图上 1:150) */
  current_scale?: number | null
  current_is_standard?: boolean | null
  current_label?: string | null
}

export const listScaleCandidates = (
  projectId: string,
  params: { only_single?: boolean; include_suspect?: boolean; page?: number; page_size?: number } = {},
): Promise<{ items: ScaleCandidateItem[]; page: number }> =>
  request(`${BASE}/${projectId}/scale-candidates`, { params })

export const confirmDrawingScale = (
  projectId: string, drawingId: string, denominator: number,
): Promise<{ success: boolean }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/scale-confirm`, {
    method: 'POST', data: { denominator },
  })


// ── 人审统一工作台(解决入口散落找不到)────────────────────────

export interface ReviewTask {
  key: string
  title: string
  count: number
  /** 为什么值得做(价值说明,帮助判断先做哪个) */
  why: string
  route: string
  anchor: string
  severity: 'high' | 'medium' | 'low'
}

export const getReviewTasks = (
  projectId: string,
): Promise<{ tasks: ReviewTask[]; total_pending: number }> =>
  request(`${BASE}/${projectId}/review-tasks`)

export const confirmScalesBatch = (
  projectId: string,
  body: { limit?: number; require_single?: boolean; require_standard?: boolean } = {},
): Promise<{ data: { confirmed: number; skipped_ambiguous: number; failed: number } }> =>
  request(`${BASE}/${projectId}/scale-confirm-batch`, { method: 'POST', data: body })


// ── 人工标定轴线基准(绕开 OCR 轴号瓶颈)────────────────────────

export interface ManualAxis {
  id?: string
  label: string
  direction: 'x' | 'y'      // x=竖向轴线(数字号) | y=横向轴线(字母号)
  x1_norm: number
  y1_norm: number
  x2_norm: number
  y2_norm: number
  spacing_to_prev_mm?: number | null   // 与上一条同向轴线的实际轴距 → 可反算比例尺
  note?: string | null
}

export const listManualAxes = (
  projectId: string, drawingId: string,
): Promise<{ axes: ManualAxis[]; count: number }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes`)

export const saveManualAxis = (
  projectId: string, drawingId: string, axis: ManualAxis,
): Promise<{ success: boolean }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes`, {
    method: 'POST', data: axis,
  })

export const deleteManualAxis = (
  projectId: string, drawingId: string, direction: string, label: string,
): Promise<{ success: boolean }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes`, {
    method: 'DELETE', params: { direction, label },
  })

/** 图上可直接选中的候选轴线(归一化坐标),供「照图选线」吸附 */
export interface AxisLineCandidate {
  direction: 'x' | 'y'
  x1_norm: number
  y1_norm: number
  x2_norm: number
  y2_norm: number
  /** true = 来自人工手描记忆(自动检出漏掉的),前端可区别着色 */
  from_memory?: boolean
}

export const listAxisLineCandidates = (
  projectId: string, drawingId: string,
): Promise<{
  candidates: AxisLineCandidate[]
  count: number
  detected: number
  from_memory: number
}> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes/line-candidates`)

/** 命名方向:决定选中的多条线按什么顺序派轴号 */
export type AxisNamingOrder =
  | 'left_to_right' | 'right_to_left' | 'top_to_bottom' | 'bottom_to_top'

export const saveManualAxesBatch = (
  projectId: string, drawingId: string,
  body: {
    lines: { x1_norm: number; y1_norm: number; x2_norm: number; y2_norm: number }[]
    direction: 'x' | 'y'
    start_label: string
    end_label?: string
    direction_order: AxisNamingOrder
    spacing_mm?: number[]
  },
): Promise<{ data: { saved: number; labels: string[] } }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes/batch`, {
    method: 'POST', data: body,
  })

/** 多选已标定的单条轴线 → 合并成一组统一重新派号 */
export const relabelManualAxes = (
  projectId: string, drawingId: string,
  body: {
    labels: string[]
    direction: 'x' | 'y'
    start_label: string
    end_label?: string
    direction_order: AxisNamingOrder
  },
): Promise<{ data: { relabeled: number; labels: string[] } }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes/relabel`, {
    method: 'POST', data: body,
  })

export const deriveScaleFromAxes = (
  projectId: string, drawingId: string,
): Promise<{ data: { scale_m_pt: number; samples: number; spread: number } }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes/derive-scale`, {
    method: 'POST',
  })


// ── 图框字段框选与记忆库(人工框一次 → 自动套用同版式)────────────

export type TitleBlockField = 'discipline' | 'drawing_no' | 'title'

export const readTitleBlockRegion = (
  projectId: string, drawingId: string,
  body: {
    field: TitleBlockField
    x1: number; y1: number; x2: number; y2: number
    remember?: boolean
    global_memory?: boolean
    /** 自动识别糊了时人工直接给值 —— 人说了算 */
    value?: string
  },
): Promise<{
  success: boolean
  /** NEEDS_CONFIRMATION = 读到原文但校验不过,需人确认 */
  error: string | null
  data: {
    value: string | null
    raw_text: string
    page_aspect: number | null
    template_id: string | null
  }
}> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/title-block/region`, {
    method: 'POST', data: body,
  })

export interface ApplyResult {
  candidates: number
  updated: number
  no_template: number
  templates_used: number
  /** 做了区域重识别的张数 / 因预算用完没试的张数(如实报出,不假装跑完) */
  ocr_used: number
  ocr_skipped: number
}

/** 批量套用是**异步**的:每张图读不到档案就要区域重识别(每次数秒),
 *  几百张要跑几分钟,同步请求必被前端超时掐断。返回 task_id,轮询取结果。 */
export const applyTitleBlockTemplates = (
  projectId: string, field: TitleBlockField = 'discipline', limit = 500,
): Promise<{ data: { task_id: string } }> =>
  request(`${BASE}/${projectId}/title-block/apply`, {
    method: 'POST', params: { field, limit },
  })

export const getApplyStatus = (
  projectId: string, taskId: string,
): Promise<{
  task_id: string
  state: 'PENDING' | 'PROGRESS' | 'SUCCESS' | 'FAILURE'
  data?: ApplyResult
  error?: string
}> => request(`${BASE}/${projectId}/title-block/apply/${taskId}`)

export interface TitleBlockTemplate {
  id: string
  field: TitleBlockField
  x1: number; y1: number; x2: number; y2: number
  page_aspect: number | null
  hit_count: number
  scope: 'project' | 'global'
  created_at: string
  last_used_at: string | null
}

export const listTitleBlockTemplates = (
  projectId: string,
): Promise<{ items: TitleBlockTemplate[]; count: number }> =>
  request(`${BASE}/${projectId}/title-block/templates`)


// ── 交叉点定位与工程坐标(选点定轴 / 跨图对齐 / 世界坐标)──────────

/** 轴线方向:x=竖向 · y=横向 · skew=斜向(放射柱网/异形平面) */
export type AxisDirection = 'x' | 'y' | 'skew'

export interface AxisIntersection {
  id?: string
  label_x: string
  label_y: string
  x_norm: number
  y_norm: number
  world_x?: number | null
  world_y?: number | null
  world_z?: number | null
  note?: string | null
}

/** 平移已标定轴线:传「拖到哪」而非「拖了多远」,避免累积误差 */
export const moveManualAxis = (
  projectId: string, drawingId: string,
  body: { label: string; direction: AxisDirection; x_norm: number; y_norm: number },
): Promise<{ data: Record<string, number> }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/manual-axes/move`, {
    method: 'POST', data: body,
  })

/** 选点定轴:点一个点 + 轴号对 → 同时生成竖向与横向轴线 */
export const saveIntersection = (
  projectId: string, drawingId: string,
  body: AxisIntersection & {
    angle_x_deg?: number
    angle_y_deg?: number
    create_axes?: boolean
  },
): Promise<{ data: { intersection_id: string; axes_created: string[] } }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/intersections`, {
    method: 'POST', data: body,
  })

export const listIntersections = (
  projectId: string, drawingId: string,
): Promise<{ intersections: AxisIntersection[]; count: number }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/intersections`)

export const deleteIntersection = (
  projectId: string, drawingId: string, labelX: string, labelY: string,
): Promise<{ success: boolean }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/intersections`, {
    method: 'DELETE', params: { label_x: labelX, label_y: labelY },
  })

export interface WorldTransform {
  scale: number
  rotation_deg: number
  tx: number
  ty: number
  z: number | null
  pairs: number
  rmse_m: number
  /** 残差过大 = 点配错或轴号重名,别当好结果用 */
  suspect: boolean
}

export const solveWorldAnchor = (
  projectId: string, drawingId: string,
): Promise<{ success: boolean; error: string | null; data: WorldTransform }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/world-anchor`)

export interface CoordinateOrigin {
  discipline: string
  drawing_id: string | null
  drawing_no?: string
  title?: string
  label_x?: string
  label_y?: string
  world_x?: number | null
  world_y?: number | null
  world_z?: number | null
  note?: string | null
}

export const listCoordinateOrigins = (
  projectId: string,
): Promise<{
  origins: CoordinateOrigin[]
  missing_disciplines: { discipline: string; drawings: number }[]
  defined: number
  total_disciplines: number
}> => request(`${BASE}/${projectId}/coordinate-origins`)

export const setCoordinateOrigin = (
  projectId: string,
  body: { discipline: string; drawing_id: string; intersection_id?: string; note?: string },
): Promise<{ data: { id: string } }> =>
  request(`${BASE}/${projectId}/coordinate-origins`, { method: 'POST', data: body })

// ── 学习闭环:标注 → 分析 → 建议 → 人审采纳 → 生效 ────────────────

export interface OptimizationStep {
  step: string
  [key: string]: unknown
}

export interface OptimizationRun {
  id: string
  trigger: string
  events_scanned: number
  findings: number
  steps_json: OptimizationStep[]
  started_at: string
  finished_at: string | null
  error: string | null
}

export interface ImprovementSuggestion {
  id: string
  category: 'vocabulary' | 'ocr_correction' | 'threshold' | 'template' | 'algorithm'
  title: string
  detail: string
  evidence: Record<string, unknown>
  impact: number
  confidence: number
  /** true = 采纳即生效;false = 需开发介入,采纳只标记待导出 */
  auto_applicable: boolean
  status: 'pending' | 'accepted' | 'rejected' | 'exported'
  created_at: string
  applied_at: string | null
}

export interface LearnedRule {
  rule_type: string
  rule_key: string
  rule_value: string
  hit_count: number
  created_at: string
}

export const runOptimization = (
  projectId: string,
): Promise<{ data: { run_id: string; scanned: number; findings: number; steps: OptimizationStep[] } }> =>
  request(`${BASE}/${projectId}/optimization/run`, { method: 'POST' })

export const listOptimizationRuns = (
  projectId: string,
): Promise<{ items: OptimizationRun[]; count: number }> =>
  request(`${BASE}/${projectId}/optimization/runs`)

export const listSuggestions = (
  projectId: string,
): Promise<{ items: ImprovementSuggestion[]; count: number; pending: number }> =>
  request(`${BASE}/${projectId}/optimization/suggestions`)

export const reviewSuggestion = (
  projectId: string, suggestionId: string, accept: boolean,
): Promise<{ data: { status: string; applied: boolean; note?: string } }> =>
  request(`${BASE}/${projectId}/optimization/suggestions/${suggestionId}/review`, {
    method: 'POST', data: { accept },
  })

export const listLearnedRules = (
  projectId: string,
): Promise<{ items: LearnedRule[]; count: number }> =>
  request(`${BASE}/${projectId}/optimization/learned-rules`)

export const exportOptimizationPackage = (
  projectId: string,
): Promise<Record<string, unknown>> =>
  request(`${BASE}/${projectId}/optimization/export`)


/** 自动已识别的轴线(带轴号),供人工标定时对照参考 —— 看得见系统认成什么才好判断该补哪条 */
export interface AutoAxis {
  label: string
  direction: 'x' | 'y'
  x1_norm: number
  y1_norm: number
  x2_norm: number
  y2_norm: number
  confidence: number | null
  extractor: string
}

export const listAutoAxes = (
  projectId: string, drawingId: string,
): Promise<{ axes: AutoAxis[]; count: number; reason?: string }> =>
  request(`${BASE}/${projectId}/drawings/${drawingId}/auto-axes`)


// ── 轴网识别(Phase I 接入系统)──────────────────────────────────
//
// 识别链路会产出三样**必须人看一眼**的东西:分区编号(§8.0.5 几何推不出)、
// 粗错坐标(RANSAC 判出的 OCR 误读)、国标校验违规。以下类型与端点即为它们的出口。

/**
 * 一图多视图的分幅串号建议。
 *
 * **不是结论**:同一立面分两幅（该串号）与一页多个独立剖面（不该串）
 * 在轴网几何上形态相同,要靠各幅图名才分得开。所以只给建议、不改轴号。
 */
export interface SplitViewNumbering {
  index: number
  /** 图面阅读顺序:自上而下、同行自左至右 */
  position: number
  start: number
  end: number
  count: number
  /** 相邻两幅搭接重复的轴线根数——**假设值**（制图惯例 1），非识别结果 */
  overlap_assumed: number
}

export interface AxisRecognitionZone {
  index: number
  /** 人工确认的分区号;null 表示没有分区号 —— 是否**待确认**看 `needs_confirmation` */
  zone_label: string | null
  /**
   * 是否还等人工确认分区号。
   *
   * §8.0.5 的分区编号**只在多分区时才用**:单分区图的轴号 `1` 就是 `1`,
   * 不存在 `1-1` vs `2-1` 撞身份,人工确认无信息可加 —— 此时恒为 false,
   * 不该再向用户要输入。
   */
  needs_confirmation: boolean
  numeric_axes: number
  alpha_axes: number
  extent: [number, number, number, number]
}

export interface AxisRecognitionOutlier {
  /** 页面位置 [x, y](pt) */
  page: [number, number]
  /** OCR 读到的工程坐标 [X, Y] —— 与其余点残差过大,等人工核对 */
  world: [number, number]
}

export interface AxisRecognitionViolation {
  rule: string
  detail: string
  text: string
  zone_index: number
  kind: string
}

export interface AxisRecognitionResult {
  drawing_id: string
  status: 'pending' | 'running' | 'ready' | 'failed'
  page_w: number | null
  page_h: number | null
  circle_count: number
  additional_count: number
  axis_count: number
  zones: AxisRecognitionZone[] | null
  anchors: Array<{ label_x: string; label_y: string; world_x: number; world_y: number; note: string }> | null
  outliers: AxisRecognitionOutlier[] | null
  violations: AxisRecognitionViolation[] | null
  transform: { scale_m_pt: number; rotation_deg: number; rmse_m: number; inliers: number } | null
  /** 一图多视图的分幅（非 §8.0.5 分区）——分幅无分区号可确认 */
  is_split_view?: boolean
  /** 跨幅连续编号的**建议方案**;未改写轴号本身,需人工确认 */
  split_view_numbering?: SplitViewNumbering[] | null
  error: string | null
  updated_at: string
}

export interface AxisRecognitionSummaryRow {
  drawing_id: string
  drawing_no: string
  title: string
  status: string
  axis_count: number
  additional_count: number
  zone_count: number
  anchor_count: number
  outlier_count: number
  violation_count: number
  transform: { rmse_m?: number } | null
  updated_at: string
}

export const startProjectAxisRecognition = (projectId: string) =>
  request(`/api/v1/projects/${projectId}/axis-recognition`, { method: 'POST' })

export const listAxisRecognition = (
  projectId: string,
): Promise<{ items: AxisRecognitionSummaryRow[]; pending: { outliers: number; violations: number; drawings: number; with_anchors: number } }> =>
  request(`/api/v1/projects/${projectId}/axis-recognition`)

export const startDrawingAxisRecognition = (drawingId: string) =>
  request(`/api/v1/drawings/${drawingId}/axis-recognition`, { method: 'POST' })

export const getDrawingAxisRecognition = (
  drawingId: string,
): Promise<AxisRecognitionResult> =>
  request(`/api/v1/drawings/${drawingId}/axis-recognition`)

/** 确认分区编号。**每个分区一次**,不是每条轴线;确认后后端会自动重跑识别 */
export const confirmAxisZoneLabel = (
  drawingId: string, zoneIndex: number, zoneLabel: string,
) =>
  request(`/api/v1/drawings/${drawingId}/axis-recognition/zones/${zoneIndex}`, {
    method: 'POST', data: { zone_label: zoneLabel },
  })

/** 分区号传播的统计（J1-3）。 */
export interface ZonePropagationStats {
  /** 去重后的**真实**锚序列组数 —— 同一分区的两个方向各算一组 */
  anchor_zones: number
  anchor_drawings?: number
  candidates?: number
  /** 写入的传播条数 */
  propagated: number
  /** 覆盖到的图纸数 */
  drawings_covered?: number
  /** 无锚可用等情形的说明 */
  note?: string
}

/**
 * 把**人工确认**的分区号经轴距序列匹配传播到其他图（J1-3）。
 *
 * §8.0.5 的分区编号几何推不出，逐张确认 1052 张不现实。实测未匹配原因中
 * 「对不上任何锚」占 91%、歧义仅 1% ⇒ 瓶颈是锚覆盖不足而非算法，
 * 所以确认少数覆盖广的锚图、其余自动继承才是有杠杆的做法。
 *
 * **幂等**：每多确认一张锚图就再跑一次，匹配面扩一片。人工确认不会被覆盖。
 */
export const propagateAxisZones = (projectId: string) =>
  request<{ success: boolean; data: ZonePropagationStats }>(
    `/api/v1/projects/${projectId}/axis-recognition/propagate-zones`,
    { method: 'POST' },
  )

/**
 * 荐锚项 —— 「该确认哪几张图最划算」（J1-3）。
 *
 * 实测未匹配原因中「对不上任何锚」占 **91%**、歧义仅 1% ⇒ 瓶颈是锚覆盖不足。
 * 人工确认一次的成本是固定的，所以该优先确认**覆盖最广**的图，
 * 而不是照单逐张确认 1052 张。
 */
export interface AnchorSuggestion {
  drawing_id: string
  drawing_no: string
  title: string
  /** 最长的一组轴距序列长度 —— 匹配按组做，这才是覆盖力 */
  max_gaps: number
  /** 方向数：**双向才能构成交点**，单向图确认了也拿不到世界坐标 */
  directions: number
  zones: number
  /** 为什么推荐它；可疑者（圆形构件被当成轴号圈）会在这里标出 */
  reason: string
}

export const getAnchorSuggestions = (
  projectId: string,
  limit = 5,
): Promise<{ success: boolean; data: { items: AnchorSuggestion[]; total: number } }> =>
  request(
    `/api/v1/projects/${projectId}/axis-recognition/anchor-suggestions?limit=${limit}`,
  )

/**
 * 未分层图的定位状态 —— 供**图纸管理页**按类成批处理。
 *
 * 与工程模型页用**同一个判据**（后端 `classify_unzoned`）。
 * 两处报的数字不一样，人就不知道该信哪个。
 */
export interface LocationStatusItem {
  drawing_id: string
  drawing_no: string
  title: string
  building_unit_key?: string
  /** cross_floor / non_standard_floor_name / no_floor_hint / no_floor_by_nature */
  reason: string
  /** 人该做什么 */
  action: string
  /** 是否真的需要人补楼层 —— 跨层图与说明类为 false */
  needs_floor_input: boolean
  /** 识别到的非标准楼层名（如「台仓」），回显给人看 */
  hint?: string
}

export interface LocationStatus {
  items: LocationStatusItem[]
  by_reason: Record<string, number>
  /** 未分层总数，照实报 */
  total: number
  /** **只数真正要人动手的** —— 说明/目录本就没有楼层，不该计进待办 */
  actionable: number
}

/** 未分层原因的中文名（与后端 REASON_* 一一对应） */
export const LOCATION_REASON_LABELS: Record<string, string> = {
  cross_floor: '跨楼层表达',
  non_standard_floor_name: '非标准楼层名',
  no_floor_hint: '无楼层线索',
  no_floor_by_nature: '本就无楼层',
}

export const getLocationStatus = (
  projectId: string,
): Promise<{ success: boolean; data: LocationStatus }> =>
  request(`/api/v1/projects/${projectId}/info/location-status`)
