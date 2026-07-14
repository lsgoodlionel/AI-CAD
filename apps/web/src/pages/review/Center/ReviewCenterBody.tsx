/**
 * 审查中心主体（projectId 已确定）：Tab 切来源 + 筛选 + 统计条 + 列表。
 * 数据来源统一走 D-05 GET .../findings 聚合端点，闭环操作走 POST .../status。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Empty, Space, Spin, Typography } from 'antd'
import HelpTip from '@/components/HelpTip'
import { listDrawings, listReviewBatches } from '@/services/drawings'
import { listProjects } from '@/services/projects'
import type {
  Finding,
  FindingListMeta,
  FindingSeverity,
  FindingStatus,
} from '@/services/findings'
import { listFindings, parseFindingId } from '@/services/findings'
import FilterBar from './FilterBar'
import FindingList from './FindingList'
import StatsBar from './StatsBar'
import type { BatchOption, DrawingOption, ReviewTabDef } from './constants'

interface ReviewCenterBodyProps {
  projectId: string
}

interface DrawingListItem {
  id: string
  drawing_no?: string
  title?: string
  filename?: string
}

function drawingLabel(d: DrawingListItem): string {
  return d.drawing_no ? `${d.drawing_no} ${d.title ?? ''}`.trim() : d.title ?? d.filename ?? d.id
}

export default function ReviewCenterBody({ projectId }: ReviewCenterBodyProps): JSX.Element {
  const [projectName, setProjectName] = useState<string>('')
  const [activeTab, setActiveTab] = useState<ReviewTabDef['key']>('all')
  const [severity, setSeverity] = useState<FindingSeverity | undefined>()
  const [status, setStatus] = useState<FindingStatus | undefined>()
  const [drawingId, setDrawingId] = useState<string | undefined>()
  const [batchId, setBatchId] = useState<string | undefined>()

  const [drawingOptions, setDrawingOptions] = useState<DrawingOption[]>([])
  const [batchOptions, setBatchOptions] = useState<BatchOption[]>([])

  const [findings, setFindings] = useState<Finding[]>([])
  const [meta, setMeta] = useState<FindingListMeta | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 项目名（仅用于标题展示，不影响主流程失败态）
  useEffect(() => {
    listProjects({ limit: 200 })
      .then((res: { items?: { id: string; name: string }[] }) => {
        const found = (res.items ?? []).find((p) => p.id === projectId)
        if (found) setProjectName(found.name)
      })
      .catch(() => undefined)
  }, [projectId])

  // 图纸/套图批次下拉选项（筛选维度用）
  useEffect(() => {
    listDrawings({ project_id: projectId, limit: 500 })
      .then((res: { items?: DrawingListItem[] }) => {
        setDrawingOptions(
          (res.items ?? []).map((d) => ({ id: d.id, label: drawingLabel(d) })),
        )
      })
      .catch(() => undefined)

    listReviewBatches({ project_id: projectId, limit: 200 })
      .then((res: { items?: { id: string; created_at: string }[] }) => {
        setBatchOptions(
          (res.items ?? []).map((b) => ({
            id: b.id,
            label: `${b.id.slice(0, 8)}（${new Date(b.created_at).toLocaleDateString('zh-CN')}）`,
          })),
        )
      })
      .catch(() => undefined)
  }, [projectId])

  const fetchFindings = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listFindings(projectId, {
        source: activeTab === 'all' ? undefined : activeTab,
        severity,
        status,
        drawing_id: drawingId,
        limit: 1000,
      })
      if (res.success) {
        setFindings(res.data)
        setMeta(res.meta)
      } else {
        setError(res.error || '问题列表加载失败')
      }
    } catch {
      setError('问题列表加载失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [projectId, activeTab, severity, status, drawingId])

  useEffect(() => {
    fetchFindings()
  }, [fetchFindings])

  // 套图批次筛选：只对 cross 来源的 source_key（"{batch_id}:..."）生效，见 FilterBar 头注
  const displayedFindings = useMemo(() => {
    if (!batchId || activeTab !== 'cross') return findings
    return findings.filter((f) => {
      const { sourceKey } = parseFindingId(f.id)
      return sourceKey.startsWith(`${batchId}:`)
    })
  }, [findings, batchId, activeTab])

  const handleUpdated = useCallback((updated: Finding) => {
    setFindings((prev) => prev.map((f) => (f.id === updated.id ? updated : f)))
  }, [])

  return (
    <div style={{ padding: 24 }}>
      <Space align="center" style={{ marginBottom: 16 }}>
        <Typography.Title level={4} style={{ marginBottom: 0 }}>
          审查中心{projectName ? ` · ${projectName}` : ''}
        </Typography.Title>
        <HelpTip
          content="合并了原「单图 AI 审图」「会审审查」「套图审查」三处入口：全项目问题按统一闭环状态机（待处理→已确认→已整改→已闭环）在这里集中处理。"
          anchor=""
        />
      </Space>

      <StatsBar meta={meta} loading={loading} />

      <FilterBar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        severity={severity}
        onSeverityChange={setSeverity}
        status={status}
        onStatusChange={setStatus}
        drawingId={drawingId}
        onDrawingChange={setDrawingId}
        drawingOptions={drawingOptions}
        batchId={batchId}
        onBatchChange={setBatchId}
        batchOptions={batchOptions}
        loading={loading}
        onRefresh={fetchFindings}
      />

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}

      {loading && findings.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      ) : !error && displayedFindings.length === 0 ? (
        <Empty description="当前筛选条件下暂无问题" style={{ padding: 60 }} />
      ) : (
        <FindingList
          projectId={projectId}
          data={displayedFindings}
          loading={loading}
          onUpdated={handleUpdated}
          showSourceColumn={activeTab === 'all'}
        />
      )}
    </div>
  )
}
