import { useNavigate } from '@umijs/max'
import { Button, Card, Empty, Space, Statistic, Tag } from 'antd'
import { FileTextOutlined } from '@ant-design/icons'
import { DRAWING_STATUS_LABEL } from '../constants'
import type { DrawingStatusCount } from '../types'

interface DrawingsCardProps {
  drawingsByStatus: DrawingStatusCount[]
}

export default function DrawingsCard({ drawingsByStatus }: DrawingsCardProps) {
  const navigate = useNavigate()
  const total = drawingsByStatus.reduce((sum, row) => sum + Number(row.cnt), 0)

  return (
    <Card
      title={
        <Space>
          <FileTextOutlined />
          图纸
        </Space>
      }
      extra={
        <Button size="small" onClick={() => navigate('/drawings')}>
          查看图纸
        </Button>
      }
      style={{ height: '100%' }}
    >
      {total === 0 ? (
        <Empty description="暂无图纸，去上传第一张图纸吧" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <>
          <Statistic title="图纸总数" value={total} suffix="张" />
          <Space wrap style={{ marginTop: 12 }}>
            {drawingsByStatus.map((row) => {
              const cfg = DRAWING_STATUS_LABEL[row.status] ?? { label: row.status, color: 'default' }
              return (
                <Tag key={row.status} color={cfg.color}>
                  {cfg.label} {row.cnt}
                </Tag>
              )
            })}
          </Space>
        </>
      )}
    </Card>
  )
}
