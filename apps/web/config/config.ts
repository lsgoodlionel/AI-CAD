import { defineConfig } from '@umijs/max'
import routes from './routes'

const apiProxyTarget = process.env.API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  // ── 基础 ────────────────────────────────────────────────────
  antd: {},
  access: {},
  model: {},
  initialState: {},
  request: {},

  // ── 布局（ProLayout）────────────────────────────────────────
  layout: {
    title: 'CAD 图纸深化管理平台',
    locale: false,
  },

  // ── 路由 ────────────────────────────────────────────────────
  routes,

  // ── 国际化（关闭，纯中文）────────────────────────────────────
  locale: {
    default: 'zh-CN',
    antd: true,
    baseNavigator: false,
  },

  // ── 代理（开发时转发到 FastAPI）──────────────────────────────
  proxy: {
    '/api': {
      target: apiProxyTarget,
      changeOrigin: true,
    },
  },

  // ── 别名 ────────────────────────────────────────────────────
  alias: {
    '@': require('path').resolve(__dirname, '../src'),
  },

  // ── 构建 ────────────────────────────────────────────────────
  npmClient: 'npm',
  hash: true,
  title: 'CAD 图纸深化管理平台',

  // ── PWA manifest 注入 ────────────────────────────────────────
  headScripts: [],
  links: [
    { rel: 'manifest', href: '/manifest.json' },
    { rel: 'apple-touch-icon', href: '/icons/icon-192x192.png' },
  ],
  metas: [
    { name: 'theme-color', content: '#1677ff' },
    { name: 'mobile-web-app-capable', content: 'yes' },
    { name: 'apple-mobile-web-app-capable', content: 'yes' },
    { name: 'apple-mobile-web-app-status-bar-style', content: 'default' },
    { name: 'apple-mobile-web-app-title', content: '图纸管理' },
  ],
})
