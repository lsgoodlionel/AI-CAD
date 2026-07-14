import { useNavigate } from '@umijs/max'
import { Button, Card, Empty, Space, Statistic } from 'antd'
import { RobotOutlined } from '@ant-design/icons'
import type { AiQualitySummary } from '../types'

interface ReviewCardProps {
  aiQuality: AiQualitySummary
}

export default function ReviewCard({ aiQuality }: ReviewCardProps) {
  const navigate = useNavigate()
  const reviewedCount = aiQuality?.reviewed_count ?? 0
  const criticalCount = aiQuality?.total_critical ?? 0
  const criticalDrawings = aiQuality?.drawings_with_critical ?? 0

  return (
    <Card
      title={
        <Space>
          <RobotOutlined />
          AI 审查
        </Space>
      }
      extra={
        <Button size="small" onClick={() => navigate('/drawings')}>
          查看审查
        </Button>
      }
      style={{ height: '100%' }}
    >
      {reviewedCount === 0 ? (
        <Empty description="尚未产生 AI 审图结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Statistic title="已审图纸" value={reviewedCount} suffix="张" />
          <Statistic
            title="强条违规"
            value={criticalCount}
            suffix={`处（${criticalDrawings} 张）`}
            valueStyle={{ color: criticalCount > 0 ? '#cf1322' : '#3f8600' }}
          />
          <Statistic
            title="平均问题数"
            value={Number(aiQuality?.avg_issues ?? 0).toFixed(1)}
            suffix="个/张"
          />
        </Space>
      )}
    </Card>
  )
}
