import { useNavigate } from '@umijs/max'
import { Button, Card, Space, Typography } from 'antd'
import { CalculatorOutlined } from '@ant-design/icons'

interface QuantityCardProps {
  projectId: string
}

export default function QuantityCard({ projectId }: QuantityCardProps) {
  const navigate = useNavigate()

  return (
    <Card
      title={
        <Space>
          <CalculatorOutlined />
          算量
        </Space>
      }
      extra={
        <Button size="small" onClick={() => navigate(`/projects/${projectId}/quantities`)}>
          查看算量
        </Button>
      }
      style={{ height: '100%' }}
    >
      <Typography.Text type="secondary">
        混凝土/模板/钢筋工程量在「算量中心」统一查看，模型构建完成后自动生成
      </Typography.Text>
    </Card>
  )
}
