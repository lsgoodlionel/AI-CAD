/**
 * UmiJS Max 运行时配置
 * - getInitialState：启动时读取 JWT → 获取当前用户信息
 * - request：全局请求配置（Auth 头、401 重定向、错误提示）
 * - layout：ProLayout 运行时配置（头像、用户名、退出）
 * - PWA：注册 Service Worker
 */
import type { RequestConfig, RunTimeLayoutConfig } from '@umijs/max'
import { history } from '@umijs/max'
import { message } from 'antd'

// ── PWA Service Worker 注册 ───────────────────────────────────────
if ('serviceWorker' in navigator && process.env.NODE_ENV === 'production') {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // SW 注册失败不影响主应用功能
    })
  })
}

// ──────────────────────────────────────────────────────────────
// JWT 解析（不验证签名，仅提取 payload 用于本地展示）
// ──────────────────────────────────────────────────────────────
function parseJwt(token: string): Record<string, any> {
  const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
  return JSON.parse(atob(base64))
}

// ──────────────────────────────────────────────────────────────
// 初始状态（启动时拉取当前用户）
// ──────────────────────────────────────────────────────────────
export async function getInitialState(): Promise<{
  currentUser?: { id: string; role: string; display_name: string } | null
}> {
  const token = localStorage.getItem('cad_token')
  if (!token) {
    // 未登录时跳转至 /login（/login 本身不触发 getInitialState 保护）
    if (location.pathname !== '/login') {
      history.push('/login')
    }
    return { currentUser: null }
  }

  try {
    const payload = parseJwt(token)
    // JWT 过期检查
    if (payload.exp && Date.now() / 1000 > payload.exp) {
      localStorage.removeItem('cad_token')
      history.push('/login')
      return { currentUser: null }
    }
    return {
      currentUser: {
        id: payload.sub,
        role: payload.role ?? '',
        display_name: payload.display_name ?? payload.sub,
      },
    }
  } catch {
    localStorage.removeItem('cad_token')
    history.push('/login')
    return { currentUser: null }
  }
}

// ──────────────────────────────────────────────────────────────
// 全局请求配置
// ──────────────────────────────────────────────────────────────
export const request: RequestConfig = {
  timeout: 30000,
  requestInterceptors: [
    (url: string, options: any) => {
      const token = localStorage.getItem('cad_token')
      return {
        url,
        options: {
          ...options,
          headers: {
            ...options.headers,
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        },
      }
    },
  ],
  responseInterceptors: [
    (response: any) => {
      if (response.status === 401) {
        localStorage.removeItem('cad_token')
        history.push('/login')
      }
      return response
    },
  ],
  errorConfig: {
    errorHandler(error: any) {
      const status = error?.response?.status
      const detail = error?.response?.data?.detail
      const msg = typeof detail === 'string' ? detail : detail?.message

      if (status === 401) {
        localStorage.removeItem('cad_token')
        history.push('/login')
        return
      }
      if (status === 403) {
        message.error(msg ?? '权限不足')
        return
      }
      if (msg) {
        message.error(msg)
      } else if (status) {
        message.error(`请求失败（${status}）`)
      }
    },
    errorThrower(res: any) {
      // 让业务层可以 catch 到带 response 的 Error
      const err: any = new Error(res?.message ?? 'request error')
      err.response = res
      throw err
    },
  },
}

// ──────────────────────────────────────────────────────────────
// ProLayout 运行时配置
// ──────────────────────────────────────────────────────────────
const ROLE_LABEL: Record<string, string> = {
  group_admin:               '集团管理员',
  group_chief_engineer:      '集团总工',
  group_deepening_director:  '深化总监',
  group_commercial_director: '商务总监',
  project_manager:           '项目经理',
  project_chief_engineer:    '项目总工',
  economist:                 '经济师',
  designer:                  '设计人员',
  site_engineer:             '现场工程师',
  labor_crew:                '班组成员',
}

export const layout: RunTimeLayoutConfig = ({ initialState }) => {
  const user = initialState?.currentUser

  return {
    // ── 头像下拉菜单 ────────────────────────────────────────────
    avatarProps: user
      ? {
          src: undefined,
          title: user.display_name,
          size: 'small',
          render: (_: any, dom: any) => dom,
        }
      : undefined,

    actionsRender: () => [],

    // ── 用户信息展示 ────────────────────────────────────────────
    rightContentRender: () =>
      user ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 13, color: '#666' }}>
            {ROLE_LABEL[user.role] ?? user.role}
          </span>
          <span style={{ fontWeight: 500 }}>{user.display_name}</span>
          <a
            onClick={() => {
              localStorage.removeItem('cad_token')
              history.push('/login')
            }}
            style={{ fontSize: 13 }}
          >
            退出
          </a>
        </div>
      ) : null,

    // ── 页脚 ────────────────────────────────────────────────────
    footerRender: () => (
      <div style={{ textAlign: 'center', padding: '12px 0', color: '#aaa', fontSize: 12 }}>
        CAD 图纸深化全过程管理平台 © {new Date().getFullYear()}
      </div>
    ),

    // ── 无权限页 ────────────────────────────────────────────────
    unAccessible: (
      <div style={{ textAlign: 'center', paddingTop: 120, color: '#999' }}>
        <h2>403 — 权限不足</h2>
        <p>您的角色无法访问此页面，请联系管理员。</p>
      </div>
    ),

    // ── 未登录时不渲染布局 ──────────────────────────────────────
    childrenRender: (children: any) => children,
  }
}
