import { defineConfig } from '@umijs/max'
import routes from './routes'

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
      target: 'http://localhost:8000',
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
})
