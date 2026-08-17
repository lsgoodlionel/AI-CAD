/**
 * 轴线标定面板 —— 人工标定轴线基准的**显式入口**。
 *
 * 背景:自动轴号识别撞到 OCR 物理上限(档案数据位置序与数值序仅 0.3% 一致;
 * 轴号圈+圈内 OCR 后逆序率 0.60→0.21,仍未过 0.15 门槛)。人指定少量基准即可绕开。
 *
 * 此前入口只藏在「明细某一行 → 预览弹窗 → 底部按钮」里,找不到。本面板直接列出
 * 「哪些图该标、标了几条」,一键进标定,并把**未标定的平面图排在最前**——
 * 轴网画在平面图上,先标平面图参考系才立得住。
 */
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Space, Spin, Table, Tag, Typography, message } from 'antd'
import { request } from '@umijs/max'
import AxisCalibrator from '@/components/AxisCalibrator'

const { Text } = Typography

interface CalibrationRow {
  drawing_id: string
  drawing_no: string
  title: string
  discipline: string
  axis_count: number
  state: 'none' | 'partial' | 'ready'
}

const STATE_TAG: Record<CalibrationRow['state'], { color: string; text: string }> = {
  none: { color: 'default', text: '未标定' },
  partial: { color: 'orange', text: '部分标定' },
  ready: { color: 'green', text: '已建参考系' },
}

interface AxisCalibrationPanelProps {
  projectId: string
}

export default function AxisCalibrationPanel({ projectId }: AxisCalibrationPanelProps) {
  const [rows, setRows] = useState<CalibrationRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [planOnly, setPlanOnly] = useState(true)
  const [target, setTarget] = useState<{ id: string; title: string } | null>(null)

  const load = () => {
    setLoading(true)
    request(`/api/v1/projects/${projectId}/axis-calibration`, {
      params: { plan_only: planOnly, page, page_size: 20 },
    })
      .then((res: { items: CalibrationRow[]; total: number }) => {
        setRows(res.items)
        setTotal(res.total)
      })
      .catch(() => message.error('轴线标定进度加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [projectId, planOnly, page])

  const done = rows.filter((r) => r.state === 'ready').length

  return (
    <Card
      id="axis-calibration"
      size="small"
      style={{ marginBottom: 16 }}
      title={
        <Space>
          <span>轴线标定基准</span>
          <Tag color="blue">本页已建参考系 {done}/{rows.length}</Tag>
        </Space>
      }
      extra={
        <Space>
          <Button size="small" onClick={() => { setPlanOnly(!planOnly); setPage(1) }}>
            {planOnly ? '显示全部图纸' : '仅看平面图'}
          </Button>
          <Button size="small" onClick={load}>刷新</Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 10 }}
        message="自动轴号识别受 OCR 限制不可用,人标少量基准即为系统建立参考系"
        description="点「去标定」打开图纸:填轴号后直接点中图上那条线即可;也可切「批量标定」连点多条线,只填起止轴号与命名方向,系统自动派号。填了相邻轴距还能反算出精确比例尺。"
      />
      {loading ? (
        <div style={{ textAlign: 'center', padding: 30 }}><Spin /></div>
      ) : (
        <Table<CalibrationRow>
          size="small"
          rowKey="drawing_id"
          dataSource={rows}
          pagination={{
            current: page, total, pageSize: 20, size: 'small',
            showTotal: (t) => `共 ${t} 张`, onChange: setPage,
          }}
          locale={{ emptyText: '暂无图纸' }}
          columns={[
            { title: '图号', dataIndex: 'drawing_no', width: 150, ellipsis: true },
            { title: '图名', dataIndex: 'title', ellipsis: true },
            {
              title: '状态', dataIndex: 'state', width: 110,
              render: (v: CalibrationRow['state']) => (
                <Tag color={STATE_TAG[v].color}>{STATE_TAG[v].text}</Tag>
              ),
            },
            {
              title: '已标轴线', dataIndex: 'axis_count', width: 90,
              render: (v: number) => (v ? `${v} 条` : <Text type="secondary">—</Text>),
            },
            {
              title: '', width: 90,
              render: (_: unknown, row) => (
                <Button
                  size="small" type="link"
                  onClick={() => setTarget({ id: row.drawing_id, title: row.title })}
                >
                  去标定
                </Button>
              ),
            },
          ]}
        />
      )}

      <AxisCalibrator
        projectId={projectId}
        drawingId={target?.id ?? null}
        title={target?.title}
        onClose={() => { setTarget(null); load() }}
      />
    </Card>
  )
}
