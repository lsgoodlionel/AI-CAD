/**
 * 数据看板统一入口（Phase D 泳道4 D-15）
 *
 * 合并原「集团看板」(/dashboard/group, isAdmin) 与「项目看板」(/dashboard/project) 两个菜单项
 * 为一个「数据看板」入口，按角色自适应：
 * - 管理员（group_admin）：默认集团视图，可切换到项目视图
 * - 非管理员：仅项目视图，不显示切换器
 *
 * 旧路径 /dashboard/group、/dashboard/project 重定向到 /dashboard（见 config/routes.ts）。
 */
import { useState } from 'react'
import { useAccess } from '@umijs/max'
import { Segmented, Space, Typography } from 'antd'
import { TeamOutlined, ProjectOutlined } from '@ant-design/icons'
import GroupDashboardView from './GroupDashboard'
import ProjectDashboardView from './ProjectDashboard'

type ViewMode = 'group' | 'project'

export default function Dashboard() {
  const access = useAccess()
  const isAdmin = !!(access as { isAdmin?: boolean }).isAdmin
  const [viewMode, setViewMode] = useState<ViewMode>(isAdmin ? 'group' : 'project')

  const showGroupView = isAdmin && viewMode === 'group'

  return (
    <div style={{ padding: 24 }}>
      <Space align="center" style={{ marginBottom: 20 }} size="large">
        <Typography.Title level={4} style={{ marginBottom: 0 }}>
          数据看板
        </Typography.Title>
        {isAdmin && (
          <Segmented
            value={viewMode}
            onChange={(value) => setViewMode(value as ViewMode)}
            options={[
              { label: '集团视图', value: 'group', icon: <TeamOutlined /> },
              { label: '项目视图', value: 'project', icon: <ProjectOutlined /> },
            ]}
          />
        )}
      </Space>

      {showGroupView ? <GroupDashboardView /> : <ProjectDashboardView />}
    </div>
  )
}
