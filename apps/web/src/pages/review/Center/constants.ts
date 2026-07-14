/**
 * 审查中心展示常量：Tab 定义 + 项目内下拉筛选用的轻量类型。
 * 严重度/状态/来源的 label/color 复用 services/findings.ts 的 *_META，不在此重复定义。
 */
import type { FindingSource } from '@/services/findings'

export interface ReviewTabDef {
  key: 'all' | FindingSource
  label: string
}

/** Tab 顺序对齐蓝图 D-06：全部/单图/跨图/会审/语义/符号 */
export const REVIEW_TABS: ReviewTabDef[] = [
  { key: 'all', label: '全部' },
  { key: 'engine', label: '单图' },
  { key: 'cross', label: '跨图' },
  { key: 'review', label: '会审' },
  { key: 'semantic', label: '语义' },
  { key: 'symbol', label: '符号' },
]

export interface DrawingOption {
  id: string
  label: string
}

export interface BatchOption {
  id: string
  label: string
}
