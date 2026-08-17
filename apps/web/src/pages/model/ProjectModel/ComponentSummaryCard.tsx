/**
 * H4 装配实体汇总卡 —— 「有信息的模型」:显示本次建模装配出的 ComponentInstance
 * 总数、按类型分布、竖向/轴网覆盖、待人审(conflict)数。自取数(按 projectId)。
 */
import { useEffect, useState } from 'react'
import { Card, Space, Statistic, Tag, Typography, Tooltip } from 'antd'
import { getModelComponents, type ModelComponentsSummary } from '@/services/projectModel'

const { Text } = Typography

const TYPE_LABEL: Record<string, string> = {
  column: '柱/桩', pile: '桩', wall: '墙', beam: '梁',
  slab: '板', pipe: '管线', equipment: '设备',
  door: '门', window: '窗',
}

interface ComponentSummaryCardProps {
  projectId: string
}

export default function ComponentSummaryCard({ projectId }: ComponentSummaryCardProps) {
  const [data, setData] = useState<ModelComponentsSummary | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    getModelComponents(projectId)
      .then((d) => alive && setData(d))
      .catch(() => alive && setFailed(true))
    return () => { alive = false }
  }, [projectId])

  if (failed || !data || data.total === 0) return null

  const byType = Object.entries(data.by_type).sort((a, b) => b[1] - a[1])
  const zPct = data.total ? Math.round((data.with_z / data.total) * 100) : 0

  return (
    <Card size="small" title="装配构件(可追溯)" style={{ marginBottom: 12 }}>
      <Space size="large" wrap>
        <Statistic title="构件总数" value={data.total} />
        <Tooltip title="低置信/冲突,进人审核对队列">
          <Statistic title="待人审" value={data.conflict}
            valueStyle={{ color: data.conflict > 0 ? '#faad14' : undefined }} />
        </Tooltip>
        <Statistic title="已确认" value={data.confirmed} />
      </Space>
      <div style={{ marginTop: 8 }}>
        <Space wrap size={4}>
          {byType.map(([t, n]) => (
            <Tag key={t}>{TYPE_LABEL[t] ?? t} {n}</Tag>
          ))}
        </Space>
      </div>
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
        竖向标高覆盖 {zPct}% · 轴网定位 {data.with_grid} · 模型 v{data.model_version}
      </Text>
    </Card>
  )
}
