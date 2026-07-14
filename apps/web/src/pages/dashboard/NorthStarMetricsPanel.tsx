/**
 * 三北极星指标面板（Phase D D-24：度量埋点接入看板）
 *
 * 展示 GET /api/v1/dashboard/north-star-metrics 返回的三个指标：
 * ①关键路径完成时长 ②建模自动触发采纳率 ③审校单条耗时。
 * 口径详见后端 services/north_star_metrics.py 模块 docstring（单一口径来源）。
 *
 * 诚实展示：任一指标样本为空时后端返回 null，此处显示"暂无数据"而非 0，
 * 避免样本不足时的假象；每张卡片附样本数，便于判断置信度。
 */
import { useEffect, useState } from 'react'
import { Card, Col, Row, Statistic, Space, Typography } from 'antd'
import { FieldTimeOutlined, ThunderboltOutlined, ClockCircleOutlined } from '@ant-design/icons'
import { getNorthStarMetrics } from '@/services/dashboard'
import HelpTip from '@/components/HelpTip'

const { Text } = Typography

interface MetricEnvelope {
  success: boolean
  data: {
    criticalPathDuration: { medianHours: number | null; sampleSize: number }
    modelAutoTriggerAdoption: { rate: number | null; sampleSize: number }
    reviewActionDuration: { medianSeconds: number | null; sampleSize: number }
  } | null
}

interface NorthStarMetricsPanelProps {
  projectId?: string
}

export default function NorthStarMetricsPanel({ projectId }: NorthStarMetricsPanelProps) {
  const [data, setData] = useState<MetricEnvelope['data']>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getNorthStarMetrics(projectId)
      .then((res: MetricEnvelope) => {
        if (!cancelled) setData(res?.success ? res.data : null)
      })
      .catch(() => {
        if (!cancelled) setData(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  const criticalPath = data?.criticalPathDuration
  const adoption = data?.modelAutoTriggerAdoption
  const reviewDuration = data?.reviewActionDuration

  return (
    <Card
      size="small"
      loading={loading}
      style={{ marginBottom: 16 }}
      title={
        <Space>
          <span>北极星指标</span>
          <HelpTip
            title="北极星指标"
            content="关键路径完成时长（首图上传→首个创效提案草稿）、建模自动触发采纳率（管线建议中被采纳的比例）、审校单条耗时（人审动作相邻间隔中位数）。样本不足时显示「暂无数据」，不做假象填充。"
            anchor=""
          />
        </Space>
      }
    >
      <Row gutter={16}>
        <Col span={8}>
          <Statistic
            title="关键路径完成时长"
            prefix={<FieldTimeOutlined />}
            value={criticalPath?.medianHours ?? '暂无数据'}
            suffix={criticalPath?.medianHours != null ? '小时（中位数）' : undefined}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            样本 {criticalPath?.sampleSize ?? 0} 个项目
          </Text>
        </Col>
        <Col span={8}>
          <Statistic
            title="建模自动触发采纳率"
            prefix={<ThunderboltOutlined />}
            value={adoption?.rate != null ? `${(adoption.rate * 100).toFixed(1)}%` : '暂无数据'}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            样本 {adoption?.sampleSize ?? 0} 条建议
          </Text>
        </Col>
        <Col span={8}>
          <Statistic
            title="审校单条耗时"
            prefix={<ClockCircleOutlined />}
            value={reviewDuration?.medianSeconds ?? '暂无数据'}
            suffix={reviewDuration?.medianSeconds != null ? '秒（中位数）' : undefined}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            样本 {reviewDuration?.sampleSize ?? 0} 次相邻动作
          </Text>
        </Col>
      </Row>
    </Card>
  )
}
