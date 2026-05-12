import { request } from '@umijs/max'

const BASE = '/api/v1/admin/engine-params'

export const getParamSchema = (scope: string) =>
  request(`${BASE}/schema/${scope}`)

export const getParams = (scope: string) =>
  request(`${BASE}/${scope}`)

export const updateParam = (scope: string, key: string, value: unknown) =>
  request(`${BASE}/${scope}/${key}`, {
    method: 'PUT',
    data: { param_key: key, param_value: value },
  })

export const resetParam = (scope: string, key: string) =>
  request(`${BASE}/${scope}/reset/${key}`, { method: 'POST' })

export const getSingleParam = (scope: string, key: string) =>
  request(`${BASE}/${scope}/value/${key}`)
