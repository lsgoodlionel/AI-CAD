import { useNavigate } from '@umijs/max'
import { Button, Card, Space, Typography } from 'antd'
import { BuildOutlined } from '@ant-design/icons'

interface ModelCardProps {
  projectId: string
  publishedDrawingCount: number
}

export default function ModelCard({ projectId, publishedDrawingCount }: ModelCardProps) {
  const navigate = useNavigate()

  return (
    <Card
      title={
        <Space>
          <BuildOutlined />
          工程建模
        </Space>
      }
      extra={
        <Button size="small" type="primary" onClick={() => navigate(`/model/${projectId}`)}>
          打开工程模型
        </Button>
      }
      style={{ height: '100%' }}
    >
      {publishedDrawingCount === 0 ? (
        <Typography.Text type="secondary">
          暂无已发布图纸，建模需要至少一张已发布图纸作为基础
        </Typography.Text>
      ) : (
        <Typography.Text type="secondary">
          已有 {publishedDrawingCount} 张图纸发布，可在工程模型页构建/查看三维模型
        </Typography.Text>
      )}
    </Card>
  )
}
