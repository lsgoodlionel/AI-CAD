// 会审审查（review）共享类型与展示 helper。
//
// 会审审查已并入 AI 审图：作为编排器第 5 引擎运行，结果随 ai_review_issues 落库，
// 在图纸详情「AI 审查报告 → 会审审查」Tab（ReviewFindings.tsx）内呈现。
// 本文件只保留被该 Tab 复用的类型与 label/color helper，不再含独立审查页的 API 调用。

// ── 会审结果结构（契约 data，中文 key 与后端逐字对应）──────────────

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

// ── 契约 V2 结构（对象识别 / 场景 / 问题包 / 文书化输出）─────────────

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

export interface DocumentClause {
  type: string
  text: string
}

export interface DocumentOutput {
  会审纪要口径: DocumentClause[]
  设计答复口径: DocumentClause[]
}

// ── 契约 V3 结构（SOP 逐项清单核查）──────────────────────────────

export interface FutureImpact {
  stage: string
  effect: string
}

export interface ChecklistItem {
  检查项: string
  命中: boolean
  覆盖: boolean
  升级: boolean
  必问问题: string
  输出口径: string
}

export interface ChecklistUncovered {
  检查项: string
  必问问题: string
  输出口径: string
  升级: boolean
}

export interface ChecklistCoverage {
  ratio: number
  checked: number
  covered: number
  items: ChecklistItem[]
  uncovered: ChecklistUncovered[]
}

/** ai_review_issues.review_sop 透传结构 */
export interface ReviewSop {
  protected_result: string
  why_now: string
  future_impact: FutureImpact
  checklist: ChecklistCoverage
}

// ── 契约 V4 结构（方法论：控制链 / 五维审查 / 结构化处理建议）──────────

export type ClosureStatus = '闭环完整' | '闭环不足'

export interface ClosureVerdict {
  status: ClosureStatus | string
  缺失项: string[]
  追问项: string[]
}

export interface ControlChain {
  触发: string
  边界: string
  风险: string
  责任: string
  动作: string[]
  闭环: string
  闭环判定: ClosureVerdict
}

export type DimensionStatus = '存疑' | '待核'

export interface DimensionRow {
  维度: string
  状态: DimensionStatus | string
  依据: string
  追问: string
}

export type ActionType = '补图' | '复核' | 'RFI' | '会签' | '专题协调'

export interface StructuredAction {
  动作: string
  动作类型: ActionType | string
  责任方: string
  配合方: string[]
  输出件: string
}

export interface ClosureRequirements {
  是否影响开工: boolean
  是否影响穿插: boolean
  是否需要专题会: boolean
  下次复核节点: string
}

export interface PriorityObjectHit {
  name: string
  weight: number
  hit: string
}

/** ai_review_issues.review_method 透传结构 */
export interface ReviewMethod {
  控制链: ControlChain
  五维审查: DimensionRow[]
  处理建议: StructuredAction[]
  闭环要求: ClosureRequirements
  优先对象: PriorityObjectHit[]
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
  对象识别?: ObjectIdentification
  场景识别?: ScenarioIdentification
  问题包?: QuestionPack
  文书输出?: DocumentOutput
  审图目标?: { protected_result: string; why_now: string }
  未来影响?: FutureImpact
  逐项清单?: ChecklistCoverage
  控制链?: ControlChain
  五维审查?: DimensionRow[]
  处理建议?: StructuredAction[]
  闭环要求?: ClosureRequirements
  优先对象?: PriorityObjectHit[]
}

// ── 展示 helper（被 ReviewFindings 复用）──────────────────────────

/** 19 专业（disciplines.yaml 全量代码） */
const DISCIPLINE_OPTIONS: { value: string; label: string }[] = [
  { value: 'ZH', label: 'ZH 综合协调' },
  { value: 'JG', label: 'JG 结构' },
  { value: 'WH', label: 'WH 围护' },
  { value: 'JZ', label: 'JZ 建筑' },
  { value: 'ZJ', label: 'ZJ 桩基' },
  { value: 'RF', label: 'RF 人防' },
  { value: 'GJG', label: 'GJG 钢结构' },
  { value: 'JDQ', label: 'JDQ 机电综合' },
  { value: 'GPS', label: 'GPS 给排水' },
  { value: 'ZS', label: 'ZS 装饰装修' },
  { value: 'DQ', label: 'DQ 电气' },
  { value: 'NT', label: 'NT 暖通' },
  { value: 'MQ', label: 'MQ 幕墙' },
  { value: 'SWT', label: 'SWT 室外总体' },
  { value: 'JGUAN', label: 'JGUAN 景观' },
  { value: 'JN', label: 'JN 节能' },
  { value: 'JK', label: 'JK 基坑' },
  { value: 'RD', label: 'RD 弱电' },
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

/** 场景识别 → Tag 颜色（场景四值） */
export const SCENARIO_COLOR: Record<string, string> = {
  图间冲突: 'volcano',
  施工落地: 'orange',
  验收风险: 'red',
  正常审图: 'green',
}

export const scenarioColor = (name?: string): string =>
  SCENARIO_COLOR[name ?? ''] ?? 'default'

/** 闭环判定 → Tag 颜色（闭环不足=red, 闭环完整=green） */
export const closureColor = (status?: string): string =>
  status === '闭环不足' ? 'red' : status === '闭环完整' ? 'green' : 'default'

/** 五维审查状态 → Tag 颜色（存疑=orange, 待核=default） */
export const dimensionColor = (status?: string): string =>
  status === '存疑' ? 'orange' : 'default'
