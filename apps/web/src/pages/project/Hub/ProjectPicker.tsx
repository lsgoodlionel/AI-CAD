/**
 * /hub（无项目 id）：项目选择器
 * 记住上次选择（localStorage），仅 1 个项目时自动进入，无项目给引导文案。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { Alert, Button, Card, Select, Space, Spin, Typography } from 'antd'
import { AppstoreOutlined } from '@ant-design/icons'
import { listProjects } from '@/services/projects'
import { LAST_PROJECT_STORAGE_KEY } from './constants'
import type { ProjectSummary } from './types'

interface ListProjectsResponse {
  items?: ProjectSummary[]
  total?: number
}

function isListProjectsResponse(value: unknown): value is ListProjectsResponse {
  return typeof value === 'object' && value !== null
}

export default function ProjectPicker() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [selectedId, setSelectedId] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listProjects({ limit: 200 })
      .then((res: unknown) => {
        if (cancelled) return
        const list = isListProjectsResponse(res) ? res.items ?? [] : []
        setProjects(list)

        if (list.length === 1) {
          navigate(`/projects/${list[0].id}/hub`, { replace: true })
          return
        }

        const lastId = window.localStorage.getItem(LAST_PROJECT_STORAGE_KEY)
        const lastStillExists = list.some((p) => p.id === lastId)
        if (lastId && lastStillExists) {
          navigate(`/projects/${lastId}/hub`, { replace: true })
          return
        }

        setSelectedId(list[0]?.id ?? '')
      })
      .catch(() => {
        if (!cancelled) setError('加载项目列表失败，请刷新重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [navigate])

  const handleEnter = (): void => {
    if (!selectedId) return
    navigate(`/projects/${selectedId}/hub`)
  }

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Spin style={{ marginTop: 80 }} />
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="error" showIcon message={error} />
      </div>
    )
  }

  if (projects.length === 0) {
    return (
      <div style={{ padding: 24 }}>
        <Card>
          <Space direction="vertical" align="center" style={{ width: '100%', padding: '48px 0' }}>
            <AppstoreOutlined style={{ fontSize: 40, color: '#bfbfbf' }} />
            <Typography.Title level={4}>暂无项目</Typography.Title>
            <Typography.Text type="secondary">
              请先联系集团管理员在「系统管理 → 项目管理」创建项目
            </Typography.Text>
          </Space>
        </Card>
      </div>
    )
  }

  return (
    <div style={{ padding: 24 }}>
      <Card>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <Typography.Title level={4} style={{ marginBottom: 0 }}>
            选择项目进入工作台
          </Typography.Title>
          <Space>
            <Select
              style={{ width: 320 }}
              placeholder="选择项目"
              value={selectedId || undefined}
              onChange={setSelectedId}
              options={projects.map((p) => ({ label: p.name, value: p.id }))}
            />
            <Button type="primary" disabled={!selectedId} onClick={handleEnter}>
              进入工作台
            </Button>
          </Space>
        </Space>
      </Card>
    </div>
  )
}
