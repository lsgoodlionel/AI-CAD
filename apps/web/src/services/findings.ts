/**
 * 统一 Finding API 封装（Phase D · 泳道2 · D-06 审查中心消费 D-05 后端聚合端点）。
 *
 * 后端 routers/findings.py 把五类割裂的问题/发现（单图 AI 审图 engine / 会审 review /
 * 跨图套图 cross / 语义审校 semantic / 符号待审 symbol）统一聚合为一个 Finding 抽象，
 * 详见 services/finding_service.py 头部注释。本文件只做类型声明 + 端点封装 + 展示
 * meta（label/color），不做业务判断。
 */
import { request } from '@umijs/max'

const BASE = '/api/v1/projects'

// ── 枚举与展示 meta ──────────────────────────────────────────────

export type FindingSource = 'engine' | 'review' | 'cross' | 'semantic' | 'symbol'
export type FindingSeverity = 'critical' | 'high' | 'medium' | 'low'
export type FindingStatus = 'pending' | 'acknowledged' | 'remediated' | 'closed'

/** 状态机固定四态、单向推进（对齐 finding_service.STATUS_ORDER），前端据此算下一态 */
export const STATUS_ORDER: FindingStatus[] = ['pending', 'acknowledged', 'remediated', 'closed']

export const SOURCE_META: Record<FindingSource, { label: string; color: string }> = {
  engine: { label: '单图', color: 'blue' },
  review: { label: '会审', color: 'purple' },
  cross: { label: '跨图', color: 'geekblue' },
  semantic: { label: '语义', color: 'cyan' },
  symbol: { label: '符号', color: 'gold' },
}

export const SEVERITY_META: Record<FindingSeverity, { label: string; color: string }> = {
  critical: { label: '致命', color: 'red' },
  high: { label: '高', color: 'volcano' },
  medium: { label: '中', color: 'orange' },
  low: { label: '低', color: 'default' },
}

export const STATUS_META: Record<FindingStatus, { label: string; color: string }> = {
  pending: { label: '待处理', color: 'default' },
  acknowledged: { label: '已确认', color: 'processing' },
  remediated: { label: '已整改', color: 'warning' },
  closed: { label: '已闭环', color: 'success' },
}

/** 状态机下一态；已是终态（closed）时返回 null */
export function nextStatus(current: FindingStatus): FindingStatus | null {
  const idx = STATUS_ORDER.indexOf(current)
  if (idx < 0 || idx >= STATUS_ORDER.length - 1) return null
  return STATUS_ORDER[idx + 1]
}

/**
 * Finding.id 形如 "engine:123" 或 "cross:{batch_id}:cluster:{key}"（source_key 内部可能
 * 再含冒号）。source 一定不含冒号，故只在首个冒号处切分即可无损还原 source_key。
 */
export function parseFindingId(id: string): { source: FindingSource; sourceKey: string } {
  const idx = id.indexOf(':')
  const source = (idx >= 0 ? id.slice(0, idx) : id) as FindingSource
  const sourceKey = idx >= 0 ? id.slice(idx + 1) : ''
  return { source, sourceKey }
}

// ── 数据结构（对齐 finding_service._finalize 返回形态，字段一字不差）──────

export interface FindingLocation {
  [key: string]: unknown
}

export interface Finding {
  id: string
  source: FindingSource
  project_id: string
  drawing_id: string | null
  severity: FindingSeverity
  title: string
  description: string
  status: FindingStatus
  location: FindingLocation | null
  note?: string | null
  status_updated_at?: string | null
  created_at?: string | null
  /** D-07：创效潜力判别（规则优先，见 finding_service._rule_based_saving_potential） */
  has_saving_potential: boolean
}

export interface FindingListMeta {
  total: number
  by_source: Record<string, number>
  by_severity: Record<string, number>
  by_status: Record<string, number>
  /** D-07：命中创效潜力判别的 Finding 数量 */
  saving_potential_count: number
  limit: number
  offset: number
}

export interface ApiEnvelope<T, M = Record<string, unknown>> {
  success: boolean
  data: T
  error: string | null
  meta: M
}

export type FindingListEnvelope = ApiEnvelope<Finding[], FindingListMeta>
export type FindingDetailEnvelope = ApiEnvelope<Finding, Record<string, unknown>>

export interface FindingStatusUpdateResult {
  id: string
  status: FindingStatus
  note: string | null
  status_updated_at: string | null
}
export type FindingStatusUpdateEnvelope = ApiEnvelope<
  FindingStatusUpdateResult,
  { project_id: string }
>

export interface ListFindingsParams {
  source?: FindingSource
  severity?: FindingSeverity
  status?: FindingStatus
  drawing_id?: string
  limit?: number
  offset?: number
}

/** GET /projects/{project_id}/findings — 列表 + 汇总统计（meta） */
export const listFindings = (projectId: string, params?: ListFindingsParams) =>
  request<FindingListEnvelope>(`${BASE}/${projectId}/findings`, { params })

/** GET /projects/{project_id}/findings/{source}/{source_key} — 单条详情 */
export const getFinding = (projectId: string, source: FindingSource, sourceKey: string) =>
  request<FindingDetailEnvelope>(
    `${BASE}/${projectId}/findings/${source}/${encodeURIComponent(sourceKey)}`,
  )

/**
 * POST .../status — 状态流转（单向：pending→acknowledged→remediated→closed）。
 * 回退会被后端拒绝并返回 409，调用方需捕获后给出友好提示（见 FindingActions.tsx）。
 */
export const updateFindingStatus = (
  projectId: string,
  source: FindingSource,
  sourceKey: string,
  body: { status: FindingStatus; note?: string },
) =>
  request<FindingStatusUpdateEnvelope>(
    `${BASE}/${projectId}/findings/${source}/${encodeURIComponent(sourceKey)}/status`,
    { method: 'POST', data: body, skipErrorHandler: true },
  )

// ── D-07：Finding → 创效提案草稿 ──────────────────────────────────

/** assess_saving_potential 判别结果（规则 or 规则+LLM 增强，见 finding_service.py） */
export interface FindingSavingAssessment {
  has_saving_potential: boolean
  source: 'rule' | 'rule+llm'
  confidence: number | null
  rationale: string | null
}

export interface FindingToProposalResult {
  proposal_id: string
  status: 'draft'
  finding_id: string
  saving_assessment: FindingSavingAssessment
}
export type FindingToProposalEnvelope = ApiEnvelope<
  FindingToProposalResult,
  { project_id: string }
>

export interface FindingToProposalBody {
  title?: string
  proposal_type?: 'A' | 'B'
  raw_saving_est?: number
  note?: string
  /** 规则未命中创效潜力时，是否尝试 ModelRouter LLM 可选增强判别（缺省 false） */
  use_llm?: boolean
}

/**
 * POST .../to-proposal — Finding → 创效提案草稿（仅 draft，不越过三审签字硬约束）。
 * 规则未命中创效潜力时后端返回 409（detail: "NO_SAVING_POTENTIAL"），调用方需捕获。
 */
export const findingToProposal = (
  projectId: string,
  source: FindingSource,
  sourceKey: string,
  body: FindingToProposalBody = {},
) =>
  request<FindingToProposalEnvelope>(
    `${BASE}/${projectId}/findings/${source}/${encodeURIComponent(sourceKey)}/to-proposal`,
    { method: 'POST', data: body, skipErrorHandler: true },
  )
