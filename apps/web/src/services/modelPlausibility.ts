/**
 * 现实合理性分析（对齐后端 services/model_plausibility.py 的 summarize()，key 一字不差）。
 *
 * 这一层回答的不是「识别得准不准」（那要等金标准人工判读），而是
 * **「模型里这些构件在现实世界里可能存在吗」** —— 纯计算，每次建模后自动跑。
 */
import { request } from '@umijs/max'

const BASE = '/api/v1/projects'

/** 严重度，按「凭什么否定它」分档，不按「看起来多离谱」分。 */
export type PlausibilitySeverity = 'impossible' | 'implausible' | 'suspect'

export interface PlausibilitySample {
  /** 构件 uid：`单体/楼层/类别/序号` */
  target: string
  kind: string
  detail: string
  /** 数字证据（实测值、阈值、代入公式的中间量），供复核 */
  evidence: Record<string, unknown>
}

export interface PlausibilityRuleResult {
  rule: string
  severity: PlausibilitySeverity
  /** 依据：规范条款号 / 定理 / 公式 */
  basis: string
  /** 命中总数（**完整计数**，不受样例截断影响） */
  count: number
  /** 按构件类别拆分的命中数 */
  kinds: Record<string, number>
  samples: PlausibilitySample[]
}

export interface PlausibilityReport {
  counts: Record<PlausibilitySeverity, number>
  rules: PlausibilityRuleResult[]
  /** 规则 id → 检查过的对象数（分母，用来算发生率） */
  checked: Record<string, number>
  /** 跑不了的规则 → 缺什么。**不等于通过**，界面必须显示 */
  skipped: Record<string, string>
  /** 规则本身抛异常 → 原因。与 skipped 分开，前者是代码 bug */
  errored: Record<string, string>
  /** 规则 id → 命中总数（仅列出样例被截断的规则） */
  truncated: Record<string, number>
  per_rule_cap: number
  model_version?: number
  analyzed_at?: string
}

interface Envelope<T> {
  success: boolean
  data: T
  error: string | null
  meta?: Record<string, unknown> | null
}

/** 读最近一次分析结果；没跑过时 data 为 null（后端不会即时重算）。 */
export async function getPlausibility(projectId: string) {
  return request<Envelope<PlausibilityReport | null>>(
    `${BASE}/${projectId}/model/plausibility`,
    { method: 'GET' },
  )
}

/** 对当前模型重新跑一遍分析。 */
export async function runPlausibility(projectId: string) {
  return request<Envelope<PlausibilityReport>>(
    `${BASE}/${projectId}/model/plausibility/run`,
    { method: 'POST' },
  )
}

/** 严重度 → 中文名与配色（与后端三档定义一一对应）。 */
export const SEVERITY_META: Record<
  PlausibilitySeverity,
  { label: string; color: string; hint: string }
> = {
  impossible: {
    label: '不可能',
    color: 'red',
    hint: '数学或物理上不成立（自交轮廓、悬浮构件、层高≤0）',
  },
  implausible: {
    label: '不合理',
    color: 'orange',
    hint: '违反规范强制下限或工程量级（截面小于规范最小值、方量超出量级）',
  },
  suspect: {
    label: '存疑',
    color: 'gold',
    hint: '统计离群，或判据所依赖的限值尚未取证',
  },
}
