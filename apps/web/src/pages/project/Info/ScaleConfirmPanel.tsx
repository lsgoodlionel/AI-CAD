/**
 * 比例尺确认面板 —— 攻 `drawing_transform` 瓶颈(覆盖率 30.5%)的人审在环入口。
 *
 * 背景:三条自动路径经真值对照全部证伪(尺寸链 3.6% / 图幅众数 50.9% / 文字自动选 24-26%),
 * 且现有变换本身质量存疑(平均置信 0.007、仅 46% 合标准比例尺)。改为「图上写明的
 * `1:N` → 高置信候选 → 人一键确认 → 精确换算落库」。实测 1085 张为唯一候选。
 */
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Space, Spin, Table, Tag, Typography, message } from 'antd'
import {
  confirmDrawingScale,
  confirmScalesBatch,
  listScaleCandidates,
  type ScaleCandidateItem,
} from '@/services/projectInfo'
import DrawingPreviewModal from '@/components/DrawingPreviewModal'

const { Text } = Typography

interface ScaleConfirmPanelProps {
  projectId: string
}

export default function ScaleConfirmPanel({ projectId }: ScaleConfirmPanelProps) {
  const [items, setItems] = useState<ScaleCandidateItem[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [onlySingle, setOnlySingle] = useState(true)
  const [suspectMode, setSuspectMode] = useState(false)
  const [preview, setPreview] = useState<{ id: string; title: string } | null>(null)
  const [batching, setBatching] = useState(false)

  // 批量确认:1310 张逐张点不现实。只处理无歧义项(唯一候选+标准比例尺,
  // 实测 93% 首选命中标准值),有歧义的留给人逐张判断。
  const runBatch = async () => {
    setBatching(true)
    try {
      const res = await confirmScalesBatch(projectId, { limit: 200 })
      const d = res.data
      message.success(`已确认 ${d.confirmed} 张(跳过有歧义 ${d.skipped_ambiguous} 张)`)
      load()
    } catch {
      message.error('批量确认失败')
    } finally {
      setBatching(false)
    }
  }

  const load = () => {
    setLoading(true)
    listScaleCandidates(projectId, {
      only_single: onlySingle && !suspectMode,
      include_suspect: suspectMode, page_size: 50,
    })
      .then((res) => setItems(res.items))
      .catch(() => message.error('比例尺候选加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [projectId, onlySingle, suspectMode])

  const confirm = async (item: ScaleCandidateItem, denominator: number) => {
    setBusy(item.drawing_id)
    try {
      await confirmDrawingScale(projectId, item.drawing_id, denominator)
      setItems((prev) => prev.filter((i) => i.drawing_id !== item.drawing_id))
      message.success(`已确认 1:${denominator}(坐标变换已落库)`)
    } catch {
      message.error('确认失败(可能无法读取图纸页面尺寸)')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card
      size="small"
      title="比例尺确认(解锁坐标变换)"
      extra={
        <Space>
          <Button size="small" type={suspectMode ? 'primary' : 'default'}
            onClick={() => setSuspectMode((v) => !v)}>
            {suspectMode ? '待确认队列' : '复核可疑变换'}
          </Button>
          {!suspectMode ? (
            <Button size="small" onClick={() => setOnlySingle((v) => !v)}>
              {onlySingle ? '显示全部' : '仅唯一候选'}
            </Button>
          ) : null}
          {!suspectMode ? (
            <Button size="small" type="primary" loading={batching} onClick={runBatch}>
              一键确认无歧义项
            </Button>
          ) : null}
          <Button size="small" onClick={load} loading={loading}>刷新</Button>
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={suspectMode
          ? '复核可疑变换:这些图已有比例尺但不是标准值(疑似算错),点候选可用图上写明的值覆盖'
          : '确认图纸比例尺,即可精确建立图纸↔实物坐标变换'}
        description="坐标变换决定构件位置、图上回投核对与训练金标签导出。系统已从图纸文字中提取「1:N」候选,确认后按物理关系精确换算(1:100 → 0.03528 米/点),无需人工量算。"
      />
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无待确认图纸" />
      ) : (
        <Table<ScaleCandidateItem>
          rowKey="drawing_id"
          size="small"
          pagination={{ pageSize: 10, showSizeChanger: false }}
          dataSource={items}
          columns={[
            {
              title: '图纸',
              dataIndex: 'drawing_no',
              width: 220,
              render: (no: string, row) => (
                <a onClick={() => setPreview({ id: row.drawing_id, title: `${no} ${row.title}` })}>
                  {no || row.title}
                </a>
              ),
            },
            { title: '图名', dataIndex: 'title', ellipsis: true },
            ...(suspectMode ? [{
              title: '现有比例尺',
              dataIndex: 'current_label',
              width: 130,
              render: (v: string | null) => (
                v ? <Tag color="red">{v} 非标准</Tag> : <Text type="secondary">—</Text>
              ),
            }] : []),
            {
              title: '比例尺候选',
              dataIndex: 'candidates',
              width: 300,
              render: (_, row) => (
                <Space wrap size={4}>
                  {row.candidates.slice(0, 4).map((c) => (
                    <Button
                      key={c.denominator}
                      size="small"
                      type={row.single ? 'primary' : 'default'}
                      ghost={row.single}
                      loading={busy === row.drawing_id}
                      onClick={() => confirm(row, c.denominator)}
                    >
                      {c.label}
                      {c.votes > 1 ? <Text type="secondary"> ×{c.votes}</Text> : null}
                      {!c.is_standard ? <Tag color="orange" style={{ marginLeft: 4 }}>非标准</Tag> : null}
                    </Button>
                  ))}
                </Space>
              ),
            },
          ]}
        />
      )}
      <DrawingPreviewModal
        drawingId={preview?.id ?? null}
        title={preview?.title}
        projectId={projectId}
        onClose={() => setPreview(null)}
      />
    </Card>
  )
}
