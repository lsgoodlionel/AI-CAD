import { request } from '@umijs/max'

const BASE = '/api/v1/drawing-review'

// ── 请求类型 ─────────────────────────────────────────────────

export interface ReviewAuditRequest {
  title: string
  body: string
  discipline?: string
  doc_type?: string
  source_db?: string
  related_disciplines?: string[]
}

export interface ReviewAuditBatchRequest {
  items: ReviewAuditRequest[]
}

export interface ListRecordsParams {
  discipline?: string
  risk_level?: string
  limit?: number
  offset?: number
}

export type DocKind = 'minutes' | 'reply'

/** 文书生成请求（CONTRACT.md V2-5：POST /document） */
export interface GenDocumentRequest {
  title: string
  body: string
  discipline?: string
  doc_kind: DocKind
}

// ── 响应类型 ─────────────────────────────────────────────────
// 契约（CONTRACT.md 第 3 节）的 data 使用中文 key。
// 这里直接以「带中文 key 的 interface」建模，避免一层手工映射带来的漂移；
// 中文 key 与后端契约逐字对应，便于审查与维护。属性访问统一通过 `data['专业判断']`。

export interface DisciplineJudgement {
  code: string
  name: string
  basis: string
}

export interface LocationInfo {
  drawings: string[]
  levels: string[]
  axes: string[]
  nodes_or_systems: string[]
  spaces: string[]
}

export interface CoreConcern {
  label: string
  reason: string
}

export interface InterfaceReview {
  primary: string
  related: string[]
  reason: string
}

export type RiskLevel = '高' | '中' | '低'

export interface RiskGrade {
  level: RiskLevel
  trigger: string
}

// ── 契约 V2 新增结构（CONTRACT.md V2-3）─────────────────────────

export type ObjectLevel = '部位级' | '系统级' | '节点级'

export interface ObjectIdentification {
  level: ObjectLevel | string
  object: string
  basis: string
}

export type ScenarioName = '正常审图' | '图间冲突' | '施工落地' | '验收风险'

export interface ScenarioIdentification {
  name: ScenarioName | string
  priority_reason: string
}

export interface QuestionPack {
  主问题: string
  补充问题: string
  证据缺口: string
}

/** 文书条目（会审纪要口径 / 设计答复口径的单条） */
export interface DocumentClause {
  type: string
  text: string
}

export interface DocumentOutput {
  会审纪要口径: DocumentClause[]
  设计答复口径: DocumentClause[]
}

export interface ReviewAuditResult {
  专业判断: DisciplineJudgement
  定位信息: LocationInfo
  核心concern: CoreConcern[]
  问题归类: string[]
  接口复核: InterfaceReview
  风险等级: RiskGrade
  建议动作: string[]
  证据缺口: string[]
  标准问题: string[]
  // 契约 V2 新增字段（旧后端可能不返回，故全部可选）
  对象识别?: ObjectIdentification
  场景识别?: ScenarioIdentification
  问题包?: QuestionPack
  文书输出?: DocumentOutput
}

// 后端统一信封后 request 通常已解包 data；保留宽松返回类型以适配两种封装。
export interface ReviewRecord extends ReviewAuditResult {
  id?: string
  title?: string
  created_at?: string
}

export interface ListRecordsResponse {
  items: ReviewRecord[]
  total: number
}

// ── API 调用 ─────────────────────────────────────────────────

export const auditText = (payload: ReviewAuditRequest): Promise<ReviewAuditResult> =>
  request(`${BASE}/audit`, { method: 'POST', data: payload })

export const auditBatch = (
  payload: ReviewAuditBatchRequest,
): Promise<ReviewAuditResult[]> =>
  request(`${BASE}/audit-batch`, { method: 'POST', data: payload })

export const listRecords = (params?: ListRecordsParams): Promise<ListRecordsResponse> =>
  request(`${BASE}/records`, { params })

/**
 * 按当前文本生成「会审纪要口径」或「设计答复口径」文书条目。
 * 返回对应口径的条目数组（DocumentClause[]）。
 */
export const genDocument = (
  payload: GenDocumentRequest,
): Promise<DocumentClause[]> =>
  request(`${BASE}/document`, { method: 'POST', data: payload })

// ── 共享常量 ─────────────────────────────────────────────────

/** 19 专业（CONTRACT.md 第 1 节 disciplines.yaml 全量代码） */
export const DISCIPLINE_OPTIONS: { value: string; label: string }[] = [
  { value: 'ZH', label: 'ZH 综合协调' },
  { value: 'JG', label: 'JG 结构' },
  { value: 'WH', label: 'WH 围护' },
  { value: 'JZ', label: 'JZ 建筑' },
  { value: 'ZJ', label: 'ZJ 桩基' },
  { value: 'RF', label: 'RF 人防' },
  { value: 'GJG', label: 'GJG 钢结构' },
  { value: 'JDQ', label: 'JDQ 机电（强电）' },
  { value: 'GPS', label: 'GPS 给排水' },
  { value: 'ZS', label: 'ZS 装饰' },
  { value: 'DQ', label: 'DQ 电气' },
  { value: 'NT', label: 'NT 暖通' },
  { value: 'MQ', label: 'MQ 幕墙' },
  { value: 'SWT', label: 'SWT 室外/市政' },
  { value: 'JGUAN', label: 'JGUAN 景观' },
  { value: 'JN', label: 'JN 节能' },
  { value: 'JK', label: 'JK 监控' },
  { value: 'RD', label: 'RD 热力' },
  { value: 'XF', label: 'XF 消防' },
]

const DISCIPLINE_LABEL: Record<string, string> = DISCIPLINE_OPTIONS.reduce(
  (acc, o) => ({ ...acc, [o.value]: o.label }),
  {} as Record<string, string>,
)

export const disciplineLabel = (code?: string): string =>
  (code && DISCIPLINE_LABEL[code]) || code || '未分类'

/** 风险等级 → Tag 颜色（高=red, 中=orange, 低=default） */
export const RISK_COLOR: Record<string, string> = {
  高: 'red',
  中: 'orange',
  低: 'default',
}

export const riskColor = (level?: string): string => RISK_COLOR[level ?? ''] ?? 'default'

/** 场景识别 → Tag 颜色（CONTRACT.md V2 场景四值） */
export const SCENARIO_COLOR: Record<string, string> = {
  图间冲突: 'volcano',
  施工落地: 'orange',
  验收风险: 'red',
  正常审图: 'green',
}

export const scenarioColor = (name?: string): string =>
  SCENARIO_COLOR[name ?? ''] ?? 'default'
