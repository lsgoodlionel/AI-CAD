/**
 * RBAC 访问控制
 * UmiJS Max 在每个路由渲染前调用此函数，access 属性与路由 access 字段对应。
 */

interface CurrentUser {
  id: string
  role: string
  display_name: string
}

export default function access(initialState: { currentUser?: CurrentUser | null }) {
  const role = initialState?.currentUser?.role ?? ''

  return {
    isLoggedIn: !!role,

    // 集团管理员：全权限
    isAdmin: role === 'group_admin',

    // 总工类角色：一审操作权
    isChiefEngineer: ['group_chief_engineer', 'project_chief_engineer', 'group_admin'].includes(role),

    // 经济类角色：二审签字权
    isEconomist: ['economist', 'group_commercial_director', 'group_admin'].includes(role),

    // 项目经理类：三审 + 提案审核权
    isPM: ['project_manager', 'group_admin'].includes(role),

    // 审图相关权限（可查看 AI 报告）
    canViewReview: role !== '',
  }
}
