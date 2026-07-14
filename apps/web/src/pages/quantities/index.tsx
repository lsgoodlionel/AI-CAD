/**
 * 算量中心（路由 /quantities 与 /projects/:id/quantities）— Phase D 泳道3 D-12
 *
 * 合并两处口径不一的算量入口：
 *   ① 工程模型页「算量汇总」（GET /projects/{id}/model/quantities，IFC-QTO）
 *   ② 图纸详情「经济测算」（钢筋翻样，drawing_economic_calcs）
 * 顶部统一展示混凝土净体积/模板面积/钢筋量同一口径，钢筋翻样明细作为下钻区。
 * 旧两处入口本批次不改，仅新增本页 + 路由；旧入口跳转链接留待集成时添加。
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from '@umijs/max'
import { Alert, Button, Empty, Space, Spin, Typography } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { getProjectQuantities } from '@/services/quantities'
import type { ProjectQtoData } from '@/services/quantities'
import ProjectSelector from './ProjectSelector'
import QtoSummaryCards from './QtoSummaryCards'
import FloorBreakdownTable from './FloorBreakdownTable'
import RebarDrilldown from './RebarDrilldown'
import { downloadQuantitiesCsv } from './exportCsv'

interface RequestLikeError {
  response?: { status?: number }
}

function isModelNotBuiltError(error: unknown): boolean {
  return (error as RequestLikeError)?.response?.status === 404
}

export default function QuantitiesCenter() {
  const params = useParams<{ id?: string }>()
  const projectId = params.id ?? ''

  if (!projectId) {
    return <ProjectSelector />
  }

  return <QuantitiesCenterBody projectId={projectId} />
}

interface QuantitiesCenterBodyProps {
  projectId: string
}

function QuantitiesCenterBody({ projectId }: QuantitiesCenterBodyProps) {
  const navigate = useNavigate()
  const [data, setData] = useState<ProjectQtoData | null>(null)
  const [loading, setLoading] = useState(true)
  const [notBuilt, setNotBuilt] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchQuantities = useCallback(async () => {
    setLoading(true)
    setError(null)
    setNotBuilt(false)
    try {
      const res = await getProjectQuantities(projectId)
      if (res.success) {
        setData(res.data)
      } else {
        setError(res.error || '算量数据加载失败')
      }
    } catch (err: unknown) {
      if (isModelNotBuiltError(err)) {
        setNotBuilt(true)
        setData(null)
      } else {
        setError('算量数据加载失败，请稍后重试')
      }
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    fetchQuantities()
  }, [fetchQuantities])

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }} align="center">
        <Typography.Title level={4} style={{ marginBottom: 0 }}>
          算量中心
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={fetchQuantities} loading={loading}>
          刷新
        </Button>
        <Button
          icon={<DownloadOutlined />}
          disabled={!data}
          onClick={() => data && downloadQuantitiesCsv(data, projectId)}
        >
          导出 CSV
        </Button>
      </Space>

      {loading && (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      )}

      {!loading && error && <Alert type="error" showIcon message={error} />}

      {!loading && notBuilt && (
        <Empty description="该项目尚未构建工程模型，暂无 IFC-QTO 算量数据">
          <Button type="primary" onClick={() => navigate(`/model/${projectId}`)}>
            前往工程模型页构建
          </Button>
        </Empty>
      )}

      {!loading && !error && !notBuilt && data && (
        <>
          <QtoSummaryCards summary={data.project} />
          <FloorBreakdownTable byFloor={data.by_floor} byBuilding={data.by_building} />
          <RebarDrilldown projectId={projectId} />
        </>
      )}
    </div>
  )
}
