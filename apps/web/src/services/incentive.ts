import { request } from '@umijs/max'

const BASE = '/api/v1/incentive/proposals'

export const listProposals = (params?: {
  project_id?: string
  proposal_type?: string
  status?: string
  limit?: number
  offset?: number
}) => request(BASE, { params })

export const getProposal = (id: string) => request(`${BASE}/${id}`)

export const submitProposal = (data: {
  project_id: string
  drawing_id?: string
  proposal_type: 'A' | 'B'
  title: string
  description: string
  raw_saving_est?: number
}) => request(BASE, { method: 'POST', data })

export const calculateSaving = (
  id: string,
  data: { net_saving: number; bonus_rate?: number; notes?: string },
) => request(`${BASE}/${id}/calculate`, { method: 'POST', data })

export const signProposal = (id: string, comment?: string) =>
  request(`${BASE}/${id}/sign`, { method: 'POST', data: { comment: comment ?? '' } })

export const distributeBonus = (
  id: string,
  team_breakdown: { user_id: string; display_name: string; amount: number }[],
) => request(`${BASE}/${id}/distribute`, { method: 'POST', data: { team_breakdown } })

export const rejectProposal = (id: string, reason: string) =>
  request(`${BASE}/${id}/reject`, { method: 'POST', data: { reason } })

export const getCertificateUrl = (id: string) =>
  `/api/v1/incentive/proposals/${id}/certificate`
