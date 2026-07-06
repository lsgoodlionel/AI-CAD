import { request } from '@umijs/max'

const BASE = '/api/v1/projects'

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
}

export interface ModelScene {
  project: SceneProject
  floors: SceneFloor[]
  markers: SceneMarker[]
  cross_links: CrossLink[]
  ifc_models: SceneIfcModel[]
  stats: SceneStats
  generated_at: string
}

// ── API 响应类型 ─────────────────────────────────────────────

export type ProjectModelStatus = 'building' | 'ready' | 'failed'

export interface ProjectModelResponse {
  status: ProjectModelStatus
  version: number
  built_at: string | null
  error: string | null
  scene: ModelScene | null
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

/** 用 MinIO 资产 key 换取 presigned URL（5 分钟有效） */
export const getModelAssetUrl = (projectId: string, key: string) =>
  request<ModelAssetUrlResult>(`${BASE}/${projectId}/model/asset-url`, {
    params: { key },
  })
