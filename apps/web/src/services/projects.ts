import { request } from '@umijs/max'

const BASE = '/api/v1/projects'

export const listProjects = (params?: Record<string, unknown>) =>
  request(BASE, { params })

export const createProject = (data: Record<string, unknown>) =>
  request(BASE, { method: 'POST', data })

export const updateProject = (id: string, data: Record<string, unknown>) =>
  request(`${BASE}/${id}`, { method: 'PATCH', data })

export const archiveProject = (id: string) =>
  request(`${BASE}/${id}`, { method: 'DELETE' })

export const listProjectMembers = (projectId: string) =>
  request(`${BASE}/${projectId}/members`)

export const addProjectMember = (projectId: string, data: Record<string, unknown>) =>
  request(`${BASE}/${projectId}/members`, { method: 'POST', data })

export const updateProjectMember = (projectId: string, memberId: string, data: Record<string, unknown>) =>
  request(`${BASE}/${projectId}/members/${memberId}`, { method: 'PATCH', data })

export const removeProjectMember = (projectId: string, memberId: string) =>
  request(`${BASE}/${projectId}/members/${memberId}`, { method: 'DELETE' })

export const listWorkZones = (projectId: string) =>
  request(`${BASE}/${projectId}/work-zones`)

export const createWorkZone = (projectId: string, data: Record<string, unknown>) =>
  request(`${BASE}/${projectId}/work-zones`, { method: 'POST', data })

export const updateWorkZone = (projectId: string, zoneId: string, data: Record<string, unknown>) =>
  request(`${BASE}/${projectId}/work-zones/${zoneId}`, { method: 'PATCH', data })
