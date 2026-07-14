import { request } from '@umijs/max'

const BASE = '/api/v1/dashboard'

export const getGroupDashboard = () => request(`${BASE}/group`)

export const getProjectDashboard = (projectId: string) =>
  request(`${BASE}/project/${projectId}`)

// Phase D · D-24：三北极星指标（关键路径时长 / 建模自动触发采纳率 / 审校单条耗时）
export const getNorthStarMetrics = (projectId?: string) =>
  request(`${BASE}/north-star-metrics`, { params: projectId ? { project_id: projectId } : {} })
