/**
 * 算量中心服务封装（Phase D 泳道3 D-12）
 *
 * 只封装算量中心新增的端点（项目级 QTO 汇总）。
 * 钢筋翻样明细复用 services/drawings.ts 的 runEconomicCalc / getEconomicCalc，
 * 项目列表复用 services/projects.ts 的 listProjects，图纸列表复用 services/drawings.ts 的 listDrawings。
 * 不在此文件重复封装，避免口径分叉。
 */
import { request } from '@umijs/max'

const BASE = '/api/v1/projects'

// ── QTO 汇总类型（对齐 services/model_qto_summary.py summarize() / build_scene_quantities()，key 一字不差）──

export interface QtoConcrete {
  gross_m3: number
  net_m3: number
}

export interface QtoFormwork {
  contact_m2: number
  free_m2: number
}

export interface QtoByTypeBucket {
  count: number
  gross_m3: number
  net_m3: number
  formwork_contact_m2: number
}

export interface QtoRebar {
  missing: boolean
  total_kg: number | null
  total_t: number | null
}

/** 项目/楼层/单体三级复用的同一汇总结构；仅 project 级带 rebar */
export interface QtoSummary {
  concrete: QtoConcrete
  formwork: QtoFormwork
  by_type: Record<string, QtoByTypeBucket>
  element_count: number
  measured_count: number
  estimated_count: number
  uncovered_count: number
  rebar?: QtoRebar
}

export interface QtoFloorSummary extends QtoSummary {
  floor_key: string
  floor_label: string
}

export interface QtoBuildingSummary extends QtoSummary {
  building_key: string
}

export interface ProjectQtoData {
  project: QtoSummary
  by_floor: QtoFloorSummary[]
  by_building: QtoBuildingSummary[]
}

export interface ProjectQtoEnvelope {
  success: boolean
  data: ProjectQtoData
  error: string | null
  meta: { scope: string }
}

/**
 * 项目级 QTO 算量汇总（混凝土净体积/模板面积/钢筋量，分楼层/分单体下钻）。
 * 后端模型未构建时返回 404 MODEL_NOT_BUILT，skipErrorHandler 交页面层展示空态引导。
 */
export const getProjectQuantities = (projectId: string) =>
  request<ProjectQtoEnvelope>(`${BASE}/${projectId}/model/quantities`, {
    skipErrorHandler: true,
  })
