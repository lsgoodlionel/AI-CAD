import { request } from '@umijs/max'

const BASE = '/api/v1/regulations'

// ── 规范文件（Books）────────────────────────────────────────

export const listBooks = (params?: {
  discipline?: string
  status?: string
  limit?: number
  offset?: number
}) => request(`${BASE}/books`, { params })

export const createBook = (data: {
  title: string
  std_no?: string
  version?: string
  discipline?: string
  publisher?: string
  effective_at?: string
}) =>
  request(`${BASE}/books`, { method: 'POST', data })

export const createBookFromPdf = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request(`${BASE}/books/import`, {
    method: 'POST',
    data: fd,
    requestType: 'form',
  })
}

export const updateBook = (id: string, data: object) =>
  request(`${BASE}/books/${id}`, { method: 'PATCH', data })

export const deleteBook = (id: string) =>
  request(`${BASE}/books/${id}`, { method: 'DELETE' })

export const publishBook = (id: string) =>
  request(`${BASE}/books/${id}/publish`, { method: 'POST' })

export const unpublishBook = (id: string) =>
  request(`${BASE}/books/${id}/unpublish`, { method: 'POST' })

export const importBookFile = (id: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request(`${BASE}/books/${id}/import`, {
    method: 'POST',
    data: fd,
    requestType: 'form',
  })
}

// ── 条文（Articles）─────────────────────────────────────────

export const listArticles = (
  bookId: string,
  params?: {
    is_mandatory?: boolean
    obligation_level?: string
    q?: string
    limit?: number
    offset?: number
  },
) => request(`${BASE}/books/${bookId}/articles`, { params })

export const getArticle = (bookId: string, articleId: string) =>
  request(`${BASE}/books/${bookId}/articles/${articleId}`)

export const createArticle = (
  bookId: string,
  data: {
    article_no: string
    title?: string
    content: string
    obligation_level?: string
    is_mandatory?: boolean
    conditions?: object[]
  },
) =>
  request(`${BASE}/books/${bookId}/articles`, { method: 'POST', data })

export const updateArticle = (bookId: string, articleId: string, data: object) =>
  request(`${BASE}/books/${bookId}/articles/${articleId}`, { method: 'PATCH', data })

export const deleteArticle = (bookId: string, articleId: string) =>
  request(`${BASE}/books/${bookId}/articles/${articleId}`, { method: 'DELETE' })

// ── 外部 API 接入源 ──────────────────────────────────────────

export const listApiSources = () => request(`${BASE}/api-sources`)

export const createApiSource = (data: {
  name: string
  endpoint_url: string
  auth_type?: string
  auth_config?: object
  sync_interval_hours?: number
}) =>
  request(`${BASE}/api-sources`, { method: 'POST', data })

export const updateApiSource = (id: string, data: object) =>
  request(`${BASE}/api-sources/${id}`, { method: 'PATCH', data })

export const deleteApiSource = (id: string) =>
  request(`${BASE}/api-sources/${id}`, { method: 'DELETE' })

// ── 搜索 ─────────────────────────────────────────────────────

export const searchRegulations = (params: {
  q: string
  discipline?: string
  limit?: number
}) => request(`${BASE}/search`, { params })
