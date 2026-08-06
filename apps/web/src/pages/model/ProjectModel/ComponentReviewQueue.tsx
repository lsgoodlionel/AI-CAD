/**
 * H4+ 构件人审核对队列 —— 低置信(conflict)构件按置信升序排队,显示来源图纸/识别途径,
 * 人工 确认/否定/改类。动作写 review_state + 埋点(收敛飞轮:auto→confirmed 单调上升)。
 */
import { useEffect, useState } from 'react'
import { Button, Card, Empty, Popconfirm, Select, Space, Tag, Typography, message } from 'antd'
import {
  getComponentReviewQueue,
  submitComponentReview,
  llmReviewComponent,
  type ComponentReviewItem,
  type ComponentLlmRecommendation,
} from '@/services/projectModel'

const { Text } = Typography

const TYPE_LABEL: Record<string, string> = {
  column: '柱', pile: '桩', wall: '墙', beam: '梁', slab: '板',
  pipe: '管线', equipment: '设备', door: '门', window: '窗',
}
const RECLASS_OPTIONS = ['column', 'pile', 'wall', 'beam', 'slab', 'pipe', 'equipment']

interface ComponentReviewQueueProps {
  projectId: string
}

export default function ComponentReviewQueue({ projectId }: ComponentReviewQueueProps) {
  const [items, setItems] = useState<ComponentReviewItem[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [recs, setRecs] = useState<Record<string, ComponentLlmRecommendation>>({})
  const [aiBusy, setAiBusy] = useState<string | null>(null)

  const aiReview = async (item: ComponentReviewItem) => {
    setAiBusy(item.id)
    try {
      const res = await llmReviewComponent(projectId, item.id)
      setRecs((prev) => ({ ...prev, [item.id]: res.recommendation }))
      if (!res.recommendation.available) message.info('大模型暂无建议(未配置或降级)')
    } catch {
      message.error('AI 复核失败')
    } finally {
      setAiBusy(null)
    }
  }

  const VERDICT_LABEL: Record<string, string> = {
    confirm: '建议确认', reject: '建议否定', reclass: '建议改类',
  }

  const load = () => {
    setLoading(true)
    getComponentReviewQueue(projectId, 50)
      .then((res) => setItems(res.queue))
      .catch(() => message.error('人审队列加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [projectId])

  const act = async (
    item: ComponentReviewItem,
    action: 'confirm' | 'reject' | 'reclass',
    newType?: string,
  ) => {
    setBusy(item.id)
    try {
      await submitComponentReview(projectId, item.id, { action, new_type: newType })
      setItems((prev) => prev.filter((i) => i.id !== item.id))   // 出队
      message.success(action === 'confirm' ? '已确认' : action === 'reject' ? '已否定' : '已改类')
    } catch {
      message.error('提交失败')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card
      size="small"
      title={`待人审构件(${items.length})`}
      extra={<Button size="small" onClick={load} loading={loading}>刷新</Button>}
      styles={{ body: { maxHeight: 460, overflow: 'auto' } }}
    >
      {items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无待核对构件" />
      ) : (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {items.map((item) => (
            <div key={item.id} style={{ borderBottom: '1px solid #f0f0f0', paddingBottom: 8 }}>
              <Space wrap size={4}>
                <Tag color="orange">{TYPE_LABEL[item.type] ?? item.type}</Tag>
                {item.grid_ref ? <Tag>{item.grid_ref}</Tag> : null}
                {item.type_label ? <Tag color="purple">{item.type_label}</Tag> : null}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  置信 {item.confidence.toFixed(2)} · 途径 {item.engines.join('/') || '—'} · {item.obs_count} 观测
                </Text>
              </Space>
              <div style={{ fontSize: 12, margin: '4px 0' }}>
                <Text type="secondary">来源图纸:</Text>{' '}
                {item.source_drawings.length
                  ? item.source_drawings.slice(0, 3).join('、') +
                    (item.source_drawings.length > 3 ? ` 等 ${item.source_drawings.length} 张` : '')
                  : <Text type="secondary">(推断,无直接来源图)</Text>}
              </div>
              <Space size={4} wrap>
                <Button size="small" type="primary" ghost loading={busy === item.id}
                  onClick={() => act(item, 'confirm')}>确认</Button>
                <Popconfirm title="确定否定(从模型移除)?" onConfirm={() => act(item, 'reject')}>
                  <Button size="small" danger>否定</Button>
                </Popconfirm>
                <Select
                  size="small" placeholder="改类为" style={{ width: 110 }}
                  options={RECLASS_OPTIONS.map((t) => ({ value: t, label: TYPE_LABEL[t] ?? t }))}
                  onChange={(v) => act(item, 'reclass', v)}
                  disabled={busy === item.id}
                />
                <Button size="small" loading={aiBusy === item.id} onClick={() => aiReview(item)}>
                  AI 复核
                </Button>
              </Space>
              {recs[item.id]?.available ? (
                <div style={{ marginTop: 4, fontSize: 12 }}>
                  <Tag color="blue">{VERDICT_LABEL[recs[item.id].verdict ?? ''] ?? 'AI'}</Tag>
                  {recs[item.id].suggested_type ? (
                    <Tag color="cyan">{TYPE_LABEL[recs[item.id].suggested_type!] ?? recs[item.id].suggested_type}</Tag>
                  ) : null}
                  <Text type="secondary">{recs[item.id].reason}</Text>
                </div>
              ) : null}
            </div>
          ))}
        </Space>
      )}
    </Card>
  )
}
