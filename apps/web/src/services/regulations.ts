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

/** 规范的三种读法各自是否就绪（①PDF 原件 ②识别全文 ③单条条文） */
export interface BookLayers {
  title: string
  std_no: string | null
  pdf: { ready: boolean; page_count: number | null }
  full_text: { ready: boolean; chars: number; method: string | null }
  articles: {
    ready: boolean
    total: number
    mandatory: number
    graphed: number
    vectored: number
  }
}

export const getBookLayers = (id: string) =>
  request<BookLayers>(`/api/v1/regulations/books/${id}/layers`)

/** 第①层：PDF 原件（presigned，前端 iframe 内嵌） */
export const previewBook = (id: string) =>
  request<{ kind: 'pdf' | 'none'; url: string | null; title: string; reason?: string }>(
    `/api/v1/regulations/books/${id}/preview`,
  )

/** 第②层：识别出的文字全文（分段，必带来路 extract_method） */
export const getBookText = (id: string, offset = 0, limit = 20000) =>
  request<{
    title: string
    std_no: string | null
    extract_method: string | null
    page_count: number | null
    text_chars: number
    offset: number
    text: string
    has_more: boolean
  }>(`/api/v1/regulations/books/${id}/text`, { params: { offset, limit } })
