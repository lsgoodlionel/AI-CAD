import { request } from '@umijs/max'

const BASE = '/api/v1/admin/llm'

// ── 提供商 ────────────────────────────────────────────────────────
export const listProviders = () => request(`${BASE}/providers`)
export const createProvider = (data: Record<string, unknown>) =>
  request(`${BASE}/providers`, { method: 'POST', data })
export const updateProvider = (id: string, data: Record<string, unknown>) =>
  request(`${BASE}/providers/${id}`, { method: 'PATCH', data })
export const deleteProvider = (id: string) =>
  request(`${BASE}/providers/${id}`, { method: 'DELETE' })
export const checkProviderHealth = (id: string) =>
  request(`${BASE}/providers/${id}/health-check`, { method: 'POST' })
export const checkAllHealth = () =>
  request(`${BASE}/providers/health-all`)

// ── 模型 ──────────────────────────────────────────────────────────
export const listModels = (providerId?: string) =>
  request(`${BASE}/models`, { params: { provider_id: providerId } })
export const createModel = (data: Record<string, unknown>) =>
  request(`${BASE}/models`, { method: 'POST', data })
export const updateModel = (id: string, data: Record<string, unknown>) =>
  request(`${BASE}/models/${id}`, { method: 'PATCH', data })
export const deleteModel = (id: string) =>
  request(`${BASE}/models/${id}`, { method: 'DELETE' })

// ── 引擎配置 ──────────────────────────────────────────────────────
export const listEngineConfigs = (engineName?: string) =>
  request(`${BASE}/engine-configs`, { params: { engine_name: engineName } })
export const listEngineNames = () =>
  request(`${BASE}/engine-configs/engines`)
export const createEngineConfig = (data: Record<string, unknown>) =>
  request(`${BASE}/engine-configs`, { method: 'POST', data })
export const updateEngineConfig = (id: string, data: Record<string, unknown>) =>
  request(`${BASE}/engine-configs/${id}`, { method: 'PATCH', data })
export const deleteEngineConfig = (id: string) =>
  request(`${BASE}/engine-configs/${id}`, { method: 'DELETE' })
export const getEngineSummary = () =>
  request(`${BASE}/engine-configs/summary`)

// ── 调用日志 ──────────────────────────────────────────────────────
export const getCostSummary = (days = 7, engineName?: string) =>
  request(`${BASE}/logs/summary`, {
    params: {
      start_date: new Date(Date.now() - days * 86400_000).toISOString().slice(0, 10),
      engine_name: engineName,
    },
  })
export const getDailyCost = (days = 30, engineName?: string) =>
  request(`${BASE}/logs/daily`, { params: { days, engine_name: engineName } })
export const getRecentErrors = (limit = 50, engineName?: string) =>
  request(`${BASE}/logs/errors`, { params: { limit, engine_name: engineName } })
export const getCBStatus = () =>
  request(`${BASE}/logs/circuit-breakers`)
