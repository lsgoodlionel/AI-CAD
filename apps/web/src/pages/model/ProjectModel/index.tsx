/**
 * 工程模型页（路由 /model 与 /model/:projectId）
 * - 无 projectId：项目选择卡片列表
 * - 有 projectId：ModelWorkspace（顶部状态条 + 浏览/审校/算量三视图模式 + 3D 视图）
 *
 * D-13 视图模式化重构：原 ~1100 行单文件拆分为 ModelWorkspace.tsx（布局）+
 * useModelWorkspaceState.ts（状态/数据）+ modes/*（各模式面板）+ review/*（审校收件箱）。
 * 见 docs/PHASE_D_BLUEPRINT.md 泳道4 D-13/D-14。
 */
import { useEffect, useState } from 'react'
import { history, useParams, useSearchParams } from '@umijs/max'
import { Card, Col, Empty, Row, Space, Spin, Typography, message } from 'antd'
import { listProjects } from '@/services/projects'
import ModelWorkspace from './ModelWorkspace'

const { Text, Title } = Typography

interface ProjectOption {
  id: string
  name: string
  code?: string
}

function ProjectPicker() {
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listProjects({ limit: 200 })
      .then((res: { items?: ProjectOption[] }) => setProjects(res.items ?? []))
      .catch(() => message.error('项目列表加载失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin tip="加载项目列表…" />
      </div>
    )
  }

  if (projects.length === 0) {
    return <Empty style={{ marginTop: 80 }} description="暂无项目" />
  }

  return (
    <div style={{ padding: 16 }}>
      <Title level={5}>选择项目查看工程模型</Title>
      <Row gutter={[16, 16]}>
        {projects.map((project) => (
          <Col key={project.id} xs={24} sm={12} md={8} lg={6}>
            <Card hoverable onClick={() => history.push(`/model/${project.id}`)} size="small">
              <Space direction="vertical" size={4}>
                <Text strong>{project.name}</Text>
                {project.code ? <Text type="secondary">{project.code}</Text> : null}
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  )
}

export default function ProjectModelPage() {
  const params = useParams<{ projectId?: string }>()
  const [searchParams] = useSearchParams()
  const focusDrawingId = searchParams.get('focus') ?? undefined

  if (!params.projectId) {
    return <ProjectPicker />
  }
  return <ModelWorkspace projectId={params.projectId} focusDrawingId={focusDrawingId} />
}
