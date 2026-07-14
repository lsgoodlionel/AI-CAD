/**
 * 工作台头部：项目名 + 切换项目下拉
 */
import { useEffect, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { Select, Space, Typography } from 'antd'
import { listProjects } from '@/services/projects'
import { LAST_PROJECT_STORAGE_KEY } from './constants'
import type { ProjectSummary } from './types'

interface HubHeaderProps {
  projectId: string
  projectName: string
}

interface ListProjectsResponse {
  items?: ProjectSummary[]
}

function isListProjectsResponse(value: unknown): value is ListProjectsResponse {
  return typeof value === 'object' && value !== null
}

export default function HubHeader({ projectId, projectName }: HubHeaderProps) {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectSummary[]>([])

  useEffect(() => {
    listProjects({ limit: 200 }).then((res: unknown) => {
      const list = isListProjectsResponse(res) ? res.items ?? [] : []
      setProjects(list)
    })
  }, [])

  const handleSwitch = (nextId: string): void => {
    window.localStorage.setItem(LAST_PROJECT_STORAGE_KEY, nextId)
    navigate(`/projects/${nextId}/hub`)
  }

  return (
    <Space style={{ marginBottom: 16 }} size="middle">
      <Typography.Title level={4} style={{ marginBottom: 0 }}>
        {projectName || '项目工作台'}
      </Typography.Title>
      <Select
        style={{ width: 240 }}
        value={projectId}
        options={projects.map((p) => ({ label: p.name, value: p.id }))}
        onChange={handleSwitch}
      />
    </Space>
  )
}
