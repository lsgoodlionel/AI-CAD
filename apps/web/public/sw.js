/**
 * Service Worker — 图纸深化管理平台 PWA
 *
 * 策略：
 * - 静态资源（JS/CSS/字体）: Cache First（版本化文件名，长效缓存）
 * - API 请求（/api/v1/）: Network First，失败时返回离线占位响应
 * - HTML 页面: Network First，失败时返回缓存版本
 * - MinIO 文件预签 URL: Network Only（签名有 TTL，不缓存）
 */

const CACHE_VERSION = 'cad-v1'
const STATIC_CACHE  = `${CACHE_VERSION}-static`
const PAGES_CACHE   = `${CACHE_VERSION}-pages`

const STATIC_EXTENSIONS = ['.js', '.css', '.woff', '.woff2', '.png', '.ico', '.svg']
const NEVER_CACHE_PATTERNS = [
  '/api/v1/',           // API 走 Network First
  'minio',              // MinIO presigned URL
  'X-Amz-Signature',   // AWS 签名参数
]

// ── Install：预缓存 Shell ────────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      cache.addAll(['/'])
    ).then(() => self.skipWaiting())
  )
})

// ── Activate：清理旧缓存 ─────────────────────────────────────────

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith('cad-') && k !== STATIC_CACHE && k !== PAGES_CACHE)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  )
})

// ── Fetch：分策略路由 ────────────────────────────────────────────

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = request.url

  // 非 GET 请求直接穿透
  if (request.method !== 'GET') return

  // 永不缓存的请求直接穿透
  if (NEVER_CACHE_PATTERNS.some((p) => url.includes(p))) return

  // 静态资源：Cache First
  if (STATIC_EXTENSIONS.some((ext) => url.includes(ext))) {
    event.respondWith(cacheFirst(request, STATIC_CACHE))
    return
  }

  // HTML 页面：Network First，离线降级
  if (request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(networkFirstWithFallback(request, PAGES_CACHE))
    return
  }
})

// ── 策略函数 ─────────────────────────────────────────────────────

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request)
  if (cached) return cached
  const response = await fetch(request)
  if (response.ok) {
    const cache = await caches.open(cacheName)
    cache.put(request, response.clone())
  }
  return response
}

async function networkFirstWithFallback(request, cacheName) {
  try {
    const response = await fetch(request)
    if (response.ok) {
      const cache = await caches.open(cacheName)
      cache.put(request, response.clone())
    }
    return response
  } catch {
    const cached = await caches.match(request)
    if (cached) return cached
    // 离线时返回 App Shell
    return caches.match('/') || new Response(
      '<h1>离线中</h1><p>请检查网络连接后刷新页面</p>',
      { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    )
  }
}
