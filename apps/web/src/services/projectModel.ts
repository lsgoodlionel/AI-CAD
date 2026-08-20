import { request } from '@umijs/max'

const BASE = '/api/v1/projects'

/** H4:装配 ComponentInstance 汇总(有信息的模型) */
export interface ModelComponentsSummary {
  project_id: string
  model_version: number
  total: number
  with_z: number
  with_grid: number
  auto: number
  conflict: number
  confirmed: number
  by_type: Record<string, number>
}

export const getModelComponents = (projectId: string): Promise<ModelComponentsSummary> =>
  request(`${BASE}/${projectId}/model/components`)

/** H4+:待人审构件(低置信 conflict)+ 来源图纸/识别途径 */
export interface ComponentReviewItem {
  id: string
  type: string
  grid_ref: string | null
  type_label: string | null
  confidence: number
  building_key: string
  source_drawings: string[]
  engines: string[]
  obs_count: number
}

export const getComponentReviewQueue = (
  projectId: string, limit = 50,
): Promise<{ queue: ComponentReviewItem[]; model_version: number }> =>
  request(`${BASE}/${projectId}/model/components/review-queue`, { params: { limit } })

export const submitComponentReview = (
  projectId: string, instanceId: string,
  body: { action: 'confirm' | 'reject' | 'reclass'; new_type?: string; note?: string },
): Promise<{ success: boolean }> =>
  request(`${BASE}/${projectId}/model/components/${instanceId}/review`, {
    method: 'POST', data: body,
  })

/** H5:大模型复核建议(available=false 表示 LLM 不可用/无建议) */
export interface ComponentLlmRecommendation {
  available: boolean
  verdict: 'confirm' | 'reject' | 'reclass' | null
  suggested_type: string | null
  reason: string
}

export const llmReviewComponent = (
  projectId: string, instanceId: string,
): Promise<{ instance_id: string; recommendation: ComponentLlmRecommendation }> =>
  request(`${BASE}/${projectId}/model/components/${instanceId}/llm-review`, { method: 'POST' })

/** H6:某图纸某类构件在装配层的证据(3D 点击 → 实体层) */
export interface ComponentsBySource {
  total: number
  confirmed: number
  conflict: number
  with_z: number
  instances: {
    id: string; grid_ref: string | null; review_state: string
    confidence: number; z_source: string | null; type_label: string | null
  }[]
}

export const getComponentsBySource = (
  projectId: string, drawingId: string, compType: string,
): Promise<ComponentsBySource> =>
  request(`${BASE}/${projectId}/model/components/by-source`, {
    params: { drawing_id: drawingId, comp_type: compType },
  })

/** H4+ 回投:构件在图纸上的归一化标记(x/y 同除 page_h,前端按显示高度换算像素) */
export interface OverlayMarker {
  id: string
  type: string
  review_state: string
  x: number
  y: number
}

export const getComponentsOverlay = (
  projectId: string, drawingId: string,
): Promise<{ available: boolean; markers: OverlayMarker[] }> =>
  request(`${BASE}/${projectId}/model/components/overlay`, {
    params: { drawing_id: drawingId },
  })

// ── scene JSON 契约类型（对齐 docs/MODEL_BASE_BLUEPRINT.md 第 4 节，key 一字不差）──

export interface SceneProject {
  id: string
  name: string
}

export interface SceneDrawing {
  drawing_id: string
  drawing_no: string
  title: string
  discipline: string
  status: string
  current_stage: string
  /** MinIO 贴图 key（projects/../model_assets/xx.png），无贴图时为 "" */
  image_key: string
  issue_count: number
  critical_count: number
}

export interface SceneFloor {
  key: string
  label: string
  elevation: number
  order: number
  drawings: SceneDrawing[]
}

// ── V2 构件级类型（对齐 docs/MODEL_PRECISION_BLUEPRINT.md 第 4 节，key 一字不差）──

/** 平面点 [x, y]（米，轴网原点坐标系） */
export type ElementPoint = number[]

/** 柱：真实轮廓挤出 */
/** 构件类型标签(C-下一步:档案 OCR 文字反哺,如 steel/curtain_wall/pile) */
/** 尺度可疑标记：该图的构件跨度远超项目中位（实测有单图跨 4176 米），
 *  多半是坐标变换算错了。**渲染照常，但不参与场景包络**——
 *  否则相机会去框那 4.8 公里，真实建筑缩成中间一小团。 */
export interface ScaleSuspectFlag {
  scale_suspect?: boolean
}

export interface ComponentTypeLabel extends ScaleSuspectFlag {
  /** steel | curtain_wall | pile | diaphragm_wall | retaining_wall | exterior_wall */
  type_label?: string
  /** 原始 OCR 文本(如"钢立柱"/"幕墙") */
  type_text?: string
}

export interface ElementColumn extends ComponentTypeLabel {
  outline: ElementPoint[]
  src: string
  /** 识别途径:rule(几何规则)/circle(圆检测)/model/fused/human */
  source?: string
  /** 圆检测桩为 'circle' */
  shape?: string
}

/** 墙：中线 path + 墙厚 */
export interface ElementWall extends ComponentTypeLabel {
  path: ElementPoint[]
  width: number
  src: string
}

/** 梁：轴线 path + 截面 宽×高 */
export interface ElementBeam extends ScaleSuspectFlag {
  path: ElementPoint[]
  width: number
  depth: number
  src: string
}

/** 板：外轮廓 + 板厚 */
export interface ElementSlab extends ScaleSuspectFlag {
  outline: ElementPoint[]
  thickness: number
  src: string
}

/** 管线：折线 path + 管径 + 专业系统 */
export interface ElementPipe extends ScaleSuspectFlag {
  path: ElementPoint[]
  dia: number
  system: string
  src: string
}

/** 设备：闭合块轮廓 + 高度 + 标注文本 */
export interface ElementEquipment extends ScaleSuspectFlag {
  outline: ElementPoint[]
  height: number
  label: string
  src: string
}

/** 楼层构件集合（schema_version=2） */
export interface SceneFloorElements {
  columns: ElementColumn[]
  walls: ElementWall[]
  beams: ElementBeam[]
  slabs: ElementSlab[]
  pipes: ElementPipe[]
  equipment: ElementEquipment[]
}

/** 楼层构件计数 */
export interface SceneElementStats {
  columns: number
  walls: number
  beams: number
  slabs: number
  pipes: number
  equipment: number
}

/** 楼层轴网（E2：配准参考轴网入 scene，坐标为米、与构件同坐标系） */
export interface SceneFloorAxes {
  x: { label: string; coord: number }[]
  y: { label: string; coord: number }[]
  source_drawing_id: string
}

/** V2 楼层：V1 字段全保留，追加 elements / element_stats / 真实标高 */
/**
 * 楼层**标高**的来源。与层高来源是两回事——偏出 11.9 米的正是标高。
 *
 * * `drawing` —— 图纸标高文本推出
 * * `override` / `level_elevation_pairing` / `manual` —— 由覆盖给定
 * * `default` —— **硬编码默认层高推的，不是图纸值**
 */
export type ElevationSource = 'drawing' | 'override' | 'default' | string

export interface SceneFloorV2 extends SceneFloor {
  /** 图纸标高文本推导的真实标高（米）；无法确定时为 null */
  elevation_m?: number | null
  /**
   * 这一层标高的来源；旧模型无此字段。
   * 一层可由多个单体贡献、来源不同，此时为 `mixed`，明细见 `elevation_sources`。
   */
  elevation_source?: ElevationSource | 'mixed'
  /** 各贡献单体的来源明细（去重且定序）；来源一致时只有一项 */
  elevation_sources?: ElevationSource[]
  /**
   * true = 该层标高是**估算/默认值**，不是图纸实测。
   * 包含「累加链上用过默认层高」的情形 —— 实测 F2 标高 4.50 完全由
   * 「F1 的默认层高 4.5」推出，此前被当作实测值显示。
   */
  elevation_estimated?: boolean
  elements?: SceneFloorElements
  element_stats?: SceneElementStats
  /** 楼层轴网（无带轴号图时为 null/缺省，前端判空不渲染） */
  axes?: SceneFloorAxes | null
}

/** 单体（南区/北区/main…）；origin 后端恒 [0,0]，布局由前端计算 */
export interface SceneBuilding {
  key: string
  label: string
  origin: number[]
  floors: SceneFloorV2[]
}

export type SceneMarkerType = 'issue' | 'cross'

export type SceneMarkerSeverity = 'critical' | 'major' | 'minor' | 'info'

export interface SceneMarkerRef {
  drawing_id: string
  issue_id?: string
}

export interface SceneMarker {
  id: string
  type: SceneMarkerType
  severity: SceneMarkerSeverity
  floor_key: string
  /** 0~1 归一化楼层平面坐标 */
  x: number
  y: number
  title: string
  discipline_code: string
  ref: SceneMarkerRef
  /** V2：所属单体 key */
  building_key?: string
}

export interface CrossLink {
  kind: string
  label: string
  floor_keys: string[]
  drawing_ids: string[]
}

export interface SceneIfcModel {
  drawing_id: string
  gltf_key: string
}

export interface SceneStats {
  total_drawings: number
  total_issues: number
  by_severity: Record<string, number>
  by_discipline: Record<string, number>
  floors: number
  ifc_skipped?: boolean
  // ── V2（schema_version=2）──
  elements_total?: Record<string, number>
  reconstruction?: 'elements' | 'texture' | 'mixed'
  buildings?: number
  yolo_equipment?: number
}

/** 建模能力档位。`partial` = **能出结果但是降级的**，不可按 full 处理。 */
export type CapabilityLevel = 'full' | 'partial' | 'none'

/**
 * 这批图能建到什么程度 + 降级说明。
 *
 * **为什么必须显示**：模型 13 层里 10 层标高是默认值硬推的，
 * 而界面上完全看不出来——用户看到的 `F6 24.9` 与从图纸读出的
 * `36.800` 长得一模一样。降级必须可见。
 */
export interface SetCapability {
  /** 有无坐标基准图（轴号圈 + 坐标标注）；none = 只有相对几何 */
  world_coords: CapabilityLevel
  /** 楼层来源；partial = 靠专业平面图图名归纳，可能缺层 */
  floors: CapabilityLevel
  /** 标高来源；none = **层高是默认值，不是图纸实测值** */
  elevations: CapabilityLevel
  can_build: boolean
  /** 直接展示给用户的降级说明原文 */
  degradations: string[]
}

/**
 * 单体归属拆解。
 *
 * **为什么要分开**:原先只报一个「未分配 1866 张（80.8%）」,
 * 而其中 959 张是目录/说明/详图/围护图——**本就没有单体归属**。
 * 混在一起报会让人去优化一个不存在的问题。
 */
export interface UnitAssignmentSummary {
  /** 从图名读出了单体 */
  assigned: number
  /** 有楼层但无单体,降级挂默认单体（可用,但需事后纠正） */
  defaulted: number
  /** 本就没有单体归属——**不计入损失** */
  not_applicable: number
  /** 既无单体又无楼层 */
  unresolved: number
  /** 真正需要处理的 = defaulted + unresolved */
  needs_attention: number
}

export interface SetCapabilityPayload {
  /** 各建模角色的图纸张数 */
  roles: Record<string, number>
  /** 从**本批图纸**学到的「编号段 → 角色」；不是硬编码的体系 */
  learned_patterns: Record<string, string>
  capability: SetCapability
  /** 单体归属拆解;旧模型无此字段 */
  unit_assignment?: UnitAssignmentSummary | null
  /** 按依赖顺序排出的处理阶段 */
  stages: Array<{ stage: number; role: string; count: number }>
}

export interface ModelScene {
  /** 缺省=V1 楼层贴图模型；2=构件级重建（buildings/elements 可用） */
  schema_version?: number
  project: SceneProject
  buildings?: SceneBuilding[]
  floors: SceneFloor[]
  markers: SceneMarker[]
  cross_links: CrossLink[]
  ifc_models: SceneIfcModel[]
  stats: SceneStats
  /** 图纸角色统计与建模能力评估；旧模型无此字段 */
  set_capability?: SetCapabilityPayload | null
  generated_at: string
}

export type SemanticNodeType =
  | 'building_unit'
  | 'sub_zone'
  | 'functional_space'
  | 'construction_zone'

export type SemanticNodeStatus = 'candidate' | 'confirmed' | 'rejected' | 'merged'

export type SemanticNodeSource = 'automatic' | 'manual' | 'legacy_inference'

export interface SemanticNode {
  id: string
  node_type: SemanticNodeType
  canonical_name: string
  normalized_key: string
  parent_id?: string | null
  status: SemanticNodeStatus
  confidence: number
  source: SemanticNodeSource
  version: number
}

export interface SemanticTreeResponse {
  version: number
  nodes: SemanticNode[]
  evidence?: unknown[]
  conflicts?: unknown[]
  unassigned_drawings?: unknown[]
}

export interface SemanticEvidence {
  id: string
  label: string
  detail: string
  score?: number
  source_drawing_id?: string
}

export interface SemanticReviewQueueItem {
  node_id: string
  title?: string
  canonical_name?: string
  node_type: SemanticNodeType
  status: SemanticNodeStatus
  current_parent_id?: string | null
  version: number
  confidence?: number
  evidence: SemanticEvidence[]
  valid_targets?: {
    merge?: string[]
    reparent?: string[]
  }
}

export type SemanticOperationType =
  | 'confirm'
  | 'reject'
  | 'rename'
  | 'merge'
  | 'split'
  | 'reparent'

export interface SemanticOperationRequest {
  operation: SemanticOperationType
  node_id: string
  version: number
  target_node_id?: string
  new_name?: string
  split_names?: string[]
}

export interface SemanticOperationResult {
  ok: boolean
  semantic_tree_version?: number
  node?: SemanticNode
  operation?: unknown
}

export interface SemanticOperationImpact {
  affected_scope: string[]
  summary: string
  rebuild_scope: 'node' | 'branch' | 'project' | 'unknown'
  fallback_reason?: string
  rebuild_required?: boolean
  affected_nodes?: string[]
  affected_drawings?: string[]
  affected_stories?: string[]
  affected_assets?: string[]
}

export type LodCapabilityMode =
  | 'review_skeleton'
  | 'architectural_massing'
  | 'realistic_proxy'

export interface LodCapabilitySummary {
  level: number
  missing_evidence: string[]
  passed_gates?: string[]
  degradation_reasons?: string[]
  fallback_reasons?: string[]
  available_modes?: LodCapabilityMode[]
}

// ── API 响应类型 ─────────────────────────────────────────────

export type ProjectModelStatus = 'building' | 'ready' | 'failed'

/** 构建实时进度（building 状态时有值） */
export interface ModelBuildProgress {
  stage: 'fetch' | 'render' | 'recognize' | 'assemble' | string
  stage_label: string
  current: string
  done: number
  total: number
  updated_at: string
}

export interface ProjectModelResponse {
  status: ProjectModelStatus
  version: number
  built_at: string | null
  error: string | null
  scene: ModelScene | null
  progress?: ModelBuildProgress | null
  semantic_tree?: SemanticTreeResponse | null
  semantic_review_queue?: SemanticReviewQueueItem[] | null
  lod_capabilities?: Record<string, LodCapabilitySummary> | null
  quality?: Record<string, unknown> | null
  building_units?: Record<string, unknown> | null
  annotation_queue?: unknown[] | null
  lod_modes?: Record<string, Record<string, unknown>> | null
}

export interface RebuildProjectModelResult {
  project_id: string
  status: 'building'
  version: number
}

export interface ModelAssetUrlResult {
  url: string
}

// ── API 调用（错误一律透传给页面层处理，含 404 MODEL_NOT_BUILT）──

/** 获取项目 3D 模型场景；无记录时后端返回 404 MODEL_NOT_BUILT（透传，页面层捕获） */
export const getProjectModel = (projectId: string) =>
  request<ProjectModelResponse>(`${BASE}/${projectId}/model`, {
    skipErrorHandler: true,
  })

/** 触发模型重建（异步 Celery 任务），返回 building 状态 */
export const rebuildProjectModel = (projectId: string) =>
  request<RebuildProjectModelResult>(`${BASE}/${projectId}/model/rebuild`, {
    method: 'POST',
  })

/** 读取语义树快照；后端未独立提供时页面可回退到主模型响应中的 semantic_tree */
export const getProjectModelSemanticGraph = (projectId: string) =>
  request<SemanticTreeResponse>(`${BASE}/${projectId}/model/semantics`, {
    skipErrorHandler: true,
  })

const semanticOperationApiPayload = (data: SemanticOperationRequest) => ({
  operation_type: data.operation,
  target_ids: [data.node_id],
  target_node_id: data.target_node_id,
  canonical_name: data.new_name,
  split_names: data.split_names,
  expected_version: data.version,
})

/** 预估语义操作影响范围，用于提交前展示重建范围 */
export const previewProjectModelSemanticImpact = (
  projectId: string,
  data: SemanticOperationRequest,
) =>
  request<SemanticOperationImpact>(`${BASE}/${projectId}/model/rebuild-impact`, {
    params: {
      node_id: data.node_id,
      target_node_id: data.target_node_id,
      operation_type: data.operation,
      expected_version: data.version,
    },
    skipErrorHandler: true,
  })

/** 提交语义树修正操作；409 版本冲突由页面层处理 */
export const applyProjectModelSemanticOperation = (
  projectId: string,
  data: SemanticOperationRequest,
) =>
  request<SemanticOperationResult>(`${BASE}/${projectId}/model/semantic-operations`, {
    method: 'POST',
    data: semanticOperationApiPayload(data),
    skipErrorHandler: true,
  })

/** 用 MinIO 资产 key 换取 presigned URL（5 分钟有效） */
export const getModelAssetUrl = (projectId: string, key: string) =>
  request<ModelAssetUrlResult>(`${BASE}/${projectId}/model/asset-url`, {
    params: { key },
  })

// ── Task 3：楼层标高人工录入/校正 ────────────────────────────────
export interface StoryHeightRow {
  scope_key: string
  story_key: string
  story_label: string
  story_order: number
  auto_elevation_m: number | null
  auto_height_m: number | null
  manual_height_m: number | null
  manual_elevation_m: number | null
  note: string | null
  /** 方向1:平面图标注恢复的标高建议(须人审,置信低者慎用) */
  suggested_elevation_m?: number | null
  suggestion_support?: number | null
  suggestion_confidence?: number | null
  suggestion_source?: string | null
}

export interface StoryHeightSaveItem {
  scope_key: string
  story_key: string
  story_order: number
  height_m: number | null
  elevation_bottom_m?: number | null
  note?: string | null
}

/** 读取楼层标高:自动识别参考值 + 人工录入值 */
export const getModelStoryHeights = (projectId: string) =>
  request<{ data: StoryHeightRow[]; meta: { count: number } }>(
    `${BASE}/${projectId}/model/story-heights`,
  )

/** 保存人工录入/校正的层高（重建后生效） */
export const saveModelStoryHeights = (projectId: string, items: StoryHeightSaveItem[]) =>
  request<{ data: { saved: number }; meta: { note: string } }>(
    `${BASE}/${projectId}/model/story-heights`,
    { method: 'POST', data: { items } },
  )

// ── Phase A / WS2：程序化 IFC + Fragments 加载相关类型（仅新增，不改现有）──
// 对齐 docs/PHASE_A_TASKS.md A-05：scene.model_ifc = {ifc_key, frag_key, build_mode, is_estimated, generated_at}
// 以及 A-08：Fragments 构件拾取返回的选中 item 形状。

/**
 * 程序化 IFC / Fragments 产物元信息（scene.model_ifc JSON 契约，唯一来源）。
 * 后端 A-03/A-04 写入；前端 A-06 依据 frag_key 拉取 .frag，缺省时回退挤出/贴图。
 * 字段按后端真实契约：model_ifc 存在时各字段都有值，故为 required
 * （frag_key 转换失败/未产出时为 null；generated_at 旧数据可能缺省）。
 */
export interface SceneModelIfc {
  /** MinIO key：projects/{id}/model_ifc/{building_key}.ifc（合规 IFC4） */
  ifc_key: string
  /** MinIO key：That Open Fragments 二进制（.frag）；转换失败/未产出为 null */
  frag_key: string | null
  /** scene 重建模式：程序化 IFC / 挤出构件 / 贴图 */
  build_mode: 'ifc' | 'elements' | 'texture'
  /** 楼层标高等是否为估算（Phase A 恒可能为 true，Phase B 恢复真实标高） */
  is_estimated: boolean
  generated_at?: string
}

/**
 * Fragments 单个 Pset：属性名 → 值。
 * 值可为字符串/数字/布尔（IFC NominalValue），保留 unknown 以便前端安全渲染。
 */
export type FragmentPsets = Record<string, Record<string, unknown>>

/**
 * Fragments 场景拾取到的构件（A-08）。
 * 由 @thatopen/fragments 模型属性/Pset 归一化而来，供属性面板与语义联动消费。
 */
export interface PickedFragmentItem {
  /** Fragments 模型内局部 id（getItemsData 主键）；无法解析时为 null */
  localId: number | null
  /** IFC expressId（部分模型与 localId 等价，保留以便对齐） */
  expressId?: number | null
  /** IFC 类型，如 IFCWALL / IFCCOLUMN；未知时为空串 */
  ifcType: string
  /** IFC GlobalId（GUID），可缺省 */
  guid?: string
  /** 构件名称（IfcRoot.Name） */
  name?: string
  /** 所属 Fragments 模型 id（多模型场景区分） */
  modelId?: string
  /** 属性集：Pset 名 → {属性名: 值} */
  psets?: FragmentPsets
}
