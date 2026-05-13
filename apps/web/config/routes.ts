import type { IRoute } from '@umijs/max'

const routes: IRoute[] = [
  // ── 登录（无主布局）─────────────────────────────────────────
  {
    path: '/login',
    component: './Login',
    layout: false,
    name: 'login',
  },

  // ── 根重定向 ─────────────────────────────────────────────────
  {
    path: '/',
    redirect: '/drawings',
  },

  // ── 数据看板 ─────────────────────────────────────────────────
  {
    name: '数据看板',
    path: '/dashboard',
    icon: 'DashboardOutlined',
    routes: [
      {
        name: '集团看板',
        path: '/dashboard/group',
        component: './dashboard/GroupDashboard',
        access: 'isAdmin',
      },
      {
        name: '项目看板',
        path: '/dashboard/project',
        component: './dashboard/ProjectDashboard',
      },
    ],
  },

  // ── 图纸管理 ─────────────────────────────────────────────────
  {
    name: '图纸管理',
    path: '/drawings',
    icon: 'FileTextOutlined',
    routes: [
      {
        name: '图纸列表',
        path: '/drawings',
        component: './drawings/DrawingList',
      },
      {
        path: '/drawings/:id',
        component: './drawings/DrawingDetail',
        hideInMenu: true,
        name: '图纸详情',
      },
    ],
  },

  // ── 创效激励 ─────────────────────────────────────────────────
  {
    name: '创效激励',
    path: '/incentive',
    icon: 'TrophyOutlined',
    routes: [
      {
        name: '提案列表',
        path: '/incentive',
        component: './incentive/ProposalList',
      },
      {
        path: '/incentive/:id',
        component: './incentive/ProposalDetail',
        hideInMenu: true,
        name: '提案详情',
      },
    ],
  },

  // ── 系统管理（group_admin 专属）──────────────────────────────
  {
    name: '系统管理',
    path: '/admin',
    icon: 'SettingOutlined',
    access: 'isAdmin',
    routes: [
      {
        name: '模型路由管理',
        path: '/admin/model-management',
        component: './admin/ModelManagement',
        icon: 'RobotOutlined',
      },
      {
        name: '引擎参数配置',
        path: '/admin/engine-params',
        component: './admin/EngineParams',
        icon: 'ControlOutlined',
      },
      {
        name: '规范知识库',
        path: '/admin/regulations',
        component: './admin/RegulationManagement',
        icon: 'BookOutlined',
      },
    ],
  },

  // ── 404 ──────────────────────────────────────────────────────
  {
    path: '*',
    component: './404',
  },
]

export default routes
