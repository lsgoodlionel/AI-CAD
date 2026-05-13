import { request } from '@umijs/max'

const BASE = '/api/v1/dashboard'

export const getGroupDashboard = () => request(`${BASE}/group`)

export const getProjectDashboard = (projectId: string) =>
  request(`${BASE}/project/${projectId}`)
