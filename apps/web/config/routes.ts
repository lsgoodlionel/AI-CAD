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
    redirect: '/hub',
  },

  // ── 项目工作台 ───────────────────────────────────────────────
  {
    name: '项目工作台',
    path: '/hub',
    icon: 'AppstoreOutlined',
    component: './project/Hub',
  },
  {
    path: '/projects/:id/hub',
    component: './project/Hub',
    hideInMenu: true,
    name: '项目工作台详情',
  },

  // ── 数据看板（Phase D D-15：集团/项目看板合并为一个角色自适应入口）───────
  {
    name: '数据看板',
    path: '/dashboard',
    icon: 'DashboardOutlined',
    component: './dashboard',
  },
  // 旧路径迁移：原集团看板 / 项目看板独立菜单项已合并，页面文件本身保留不删。
  {
    path: '/dashboard/group',
    redirect: '/dashboard',
    hideInMenu: true,
    name: '集团看板（已迁移）',
  },
  {
    path: '/dashboard/project',
    redirect: '/dashboard',
    hideInMenu: true,
    name: '项目看板（已迁移）',
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
      // 套图审查「列表入口」已并入「审查中心」（D-06）：列表旧路由重定向到 /review。
      // 但「某个批次详情」保留独立页，避免站内「查看刚创建批次」深链丢失 batch id。
      {
        path: '/drawings/review-batches',
        redirect: '/review',
      },
      {
        path: '/drawings/review-batches/:id',
        component: './drawings/ReviewBatch/Detail',
        hideInMenu: true,
        name: '套图详情',
      },
      {
        path: '/drawings/:id',
        component: './drawings/DrawingDetail',
        hideInMenu: true,
        name: '图纸详情',
      },
    ],
  },

  // ── 工程信息（Phase E1：图纸抽取信息聚合，每条信息溯源到源图纸）─────
  {
    name: '工程信息',
    path: '/project-info',
    icon: 'ProfileOutlined',
    component: './project/Info',
  },
  {
    path: '/project-info/:projectId',
    component: './project/Info',
    hideInMenu: true,
    name: '工程信息详情',
  },

  // ── 审查中心（Phase D D-06：合并单图 AI 审图/会审审查/套图审查三处入口）─────
  {
    name: '审查中心',
    path: '/review',
    icon: 'FileSearchOutlined',
    component: './review/Center',
  },
  {
    path: '/projects/:id/review',
    component: './review/Center',
    hideInMenu: true,
    name: '审查中心详情',
  },

  // ── 工程模型 ─────────────────────────────────────────────────
  {
    name: '工程模型',
    path: '/model',
    icon: 'build',
    component: './model/ProjectModel',
  },
  {
    path: '/model/:projectId',
    component: './model/ProjectModel',
    hideInMenu: true,
    name: '工程模型详情',
  },

  // ── 算量中心（Phase D D-12：合并 IFC-QTO 汇总 + 钢筋翻样明细）───────
  {
    name: '算量中心',
    path: '/quantities',
    icon: 'CalculatorOutlined',
    component: './quantities',
  },
  {
    path: '/projects/:id/quantities',
    component: './quantities',
    hideInMenu: true,
    name: '算量中心详情',
  },

  // 会审审查已并入「图纸管理 → AI 审查报告 → 会审审查」Tab，不再独立成模块。

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
        name: '项目管理',
        path: '/admin/projects',
        component: './admin/ProjectManagement',
        icon: 'ProjectOutlined',
      },
      {
        name: '人员管理',
        path: '/admin/users',
        component: './admin/UserManagement',
        icon: 'TeamOutlined',
      },
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

  // ── 帮助中心（全体登录用户可见；管理员手册页内按角色切换）────────
  {
    name: '帮助中心',
    path: '/help',
    icon: 'QuestionCircleOutlined',
    component: './Help',
  },

  // ── 404 ──────────────────────────────────────────────────────
  {
    path: '*',
    component: './404',
  },
]

export default routes
