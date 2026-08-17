/**
 * H6 构件实体层证据 —— 3D 点击单构件时,显示该来源图纸的该类构件在**装配层(Phase H
 * 实体中心)**的证据:数量 + 审核态(已确认/待人审)+ 竖向覆盖。把 3D 点击接到「有信息
 * 的模型」的实体知识,而非只到来源图纸。
 */
import { useEffect, useState } from 'react'
import { Alert, Space, Spin, Tag, Typography } from 'antd'
import { getComponentsBySource, type ComponentsBySource } from '@/services/projectModel'

const { Text } = Typography

/** scene elementType → 实体 type(migration 033) */
const ELEMENT_TYPE_TO_COMP: Record<string, string> = {
  columns: 'column', walls: 'wall', beams: 'beam',
  slabs: 'slab', equipment: 'equipment',
}
function toCompType(elementType: string): string | null {
  if (elementType.startsWith('pipes:')) return 'pipe'
  return ELEMENT_TYPE_TO_COMP[elementType] ?? null
}

interface ComponentEntityEvidenceProps {
  projectId: string
  drawingId: string
  elementType: string
}

export default function ComponentEntityEvidence({
  projectId, drawingId, elementType,
}: ComponentEntityEvidenceProps) {
  const compType = toCompType(elementType)
  const [data, setData] = useState<ComponentsBySource | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!compType) return
    let alive = true
    setLoading(true)
    getComponentsBySource(projectId, drawingId, compType)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [projectId, drawingId, compType])

  if (!compType) return null
  if (loading) return <Spin size="small" style={{ marginTop: 12 }} />
  if (!data || data.total === 0) return null

  return (
    <Alert
      style={{ marginTop: 12 }}
      type={data.conflict > 0 ? 'warning' : 'success'}
      message="装配层证据(有信息的模型)"
      description={
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Text style={{ fontSize: 12 }}>
            此图该类构件装配 <Text strong>{data.total}</Text> 个 ·
            已确认 <Text strong>{data.confirmed}</Text> ·
            待人审 <Text type="warning">{data.conflict}</Text> ·
            有真实标高 {data.with_z}
          </Text>
          <Space wrap size={4}>
            {data.instances.slice(0, 8).map((i) => (
              <Tag key={i.id} color={i.review_state === 'confirmed' ? 'green'
                : i.review_state === 'conflict' ? 'orange' : 'default'}>
                {i.grid_ref || '无轴网'} {i.confidence.toFixed(2)}
              </Tag>
            ))}
          </Space>
        </Space>
      }
    />
  )
}
