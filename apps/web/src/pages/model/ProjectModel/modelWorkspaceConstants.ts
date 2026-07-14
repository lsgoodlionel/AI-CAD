/**
 * ModelWorkspace 展示层常量（标签映射 / 轮询间隔），从原 index.tsx 顶层原样迁出。
 */
import type { ProjectModelStatus } from '@/services/projectModel'
import type { ModelQualitySummary } from './types'

export const POLL_INTERVAL_MS = 5000

export const DISCIPLINE_LABEL: Record<string, string> = {
  structure: '结构',
  architecture: '建筑',
  mep: '机电',
  decoration: '装修',
  other: '其他',
}

export const SEVERITY_META: Record<string, { label: string; color: string }> = {
  critical: { label: '严重', color: '#f5222d' },
  major: { label: '较大', color: '#fa8c16' },
  minor: { label: '一般', color: '#faad14' },
  info: { label: '提示', color: '#8c8c8c' },
}

export const ALL_SEVERITIES = ['critical', 'major', 'minor', 'info']

export const MARKER_TYPE_LABEL: Record<string, string> = {
  issue: '图内问题',
  cross: '跨图发现',
}

export const ALL_MARKER_TYPES = ['issue', 'cross']

export const MODEL_STATUS_META: Record<
  ProjectModelStatus,
  { badge: 'processing' | 'success' | 'error'; text: string }
> = {
  building: { badge: 'processing', text: '构建中' },
  ready: { badge: 'success', text: '已就绪' },
  failed: { badge: 'error', text: '构建失败' },
}

export const EMPTY_QUALITY: ModelQualitySummary = {
  unassignedStoryCount: 0,
  floorConflictCount: 0,
  floorConflicts: [],
  lowConfidenceUnits: [],
  pendingManualCount: 0,
  pendingCandidateCount: 0,
  semanticConflictCount: 0,
}

export const RECONSTRUCTION_LABEL: Record<string, string> = {
  elements: '构件级重建',
  texture: '贴图级',
  mixed: '混合级',
}
