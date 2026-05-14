import { request } from '@umijs/max'

const BASE = '/api/v1/admin'

export const listUsers = (params?: Record<string, unknown>) =>
  request(`${BASE}/users`, { params })

export const createUser = (data: Record<string, unknown>) =>
  request(`${BASE}/users`, { method: 'POST', data })

export const updateUser = (id: string, data: Record<string, unknown>) =>
  request(`${BASE}/users/${id}`, { method: 'PATCH', data })

export const resetUserPassword = (id: string, password: string) =>
  request(`${BASE}/users/${id}/reset-password`, { method: 'POST', data: { password } })

export const enableUser = (id: string) =>
  request(`${BASE}/users/${id}/enable`, { method: 'POST' })

export const disableUser = (id: string) =>
  request(`${BASE}/users/${id}/disable`, { method: 'POST' })

export const listOrganizations = () =>
  request(`${BASE}/organizations`)

export const createOrganization = (data: Record<string, unknown>) =>
  request(`${BASE}/organizations`, { method: 'POST', data })

export const updateOrganization = (id: string, data: Record<string, unknown>) =>
  request(`${BASE}/organizations/${id}`, { method: 'PATCH', data })
