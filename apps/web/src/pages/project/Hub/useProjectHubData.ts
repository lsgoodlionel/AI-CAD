/**
 * 拉取单项目看板数据（工作台数据源），处理加载/错误态。
 */
import { useEffect, useState } from 'react'
import { getProjectDashboard } from '@/services/dashboard'
import type { ProjectDashboardData } from './types'

interface UseProjectHubDataResult {
  data: ProjectDashboardData | null
  loading: boolean
  error: string | null
}

function isProjectDashboardData(value: unknown): value is ProjectDashboardData {
  return typeof value === 'object' && value !== null && 'drawings_by_status' in value
}

export function useProjectHubData(projectId: string): UseProjectHubDataResult {
  const [data, setData] = useState<ProjectDashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!projectId) {
      setData(null)
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    getProjectDashboard(projectId)
      .then((res: unknown) => {
        if (cancelled) return
        if (!isProjectDashboardData(res)) {
          setError('项目工作台数据格式异常')
          return
        }
        setData(res)
      })
      .catch(() => {
        if (!cancelled) setError('加载项目工作台数据失败，请刷新重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [projectId])

  return { data, loading, error }
}
