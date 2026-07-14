/**
 * 算量中心下钻区：钢筋翻样明细。
 * 选择本项目内已计算过翻样的图纸，只读展示其结果；完整录入/重算入口留在图纸详情
 * （services/drawings.ts 的 runEconomicCalc / getEconomicCalc 是唯一口径来源，此处仅复用只读展示）。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { Alert, Button, Card, Col, Empty, Row, Select, Space, Spin, Statistic, Typography } from 'antd'
import { CalculatorOutlined } from '@ant-design/icons'
import { getEconomicCalc, listDrawings } from '@/services/drawings'

interface DrawingOption {
  id: string
  drawing_no: string
  title: string
}

interface ListDrawingsResponse {
  items?: DrawingOption[]
}

interface RebarCalcResult {
  exists?: boolean
  total_steel_kg?: number
  field_waste_rate?: number
  optimized_waste_rate?: number
  saving_kg?: number
  saving_yuan?: number
  auto_proposal_eligible?: boolean
  calculated_at?: string
}

function isListDrawingsResponse(value: unknown): value is ListDrawingsResponse {
  return typeof value === 'object' && value !== null
}

interface RebarDrilldownProps {
  projectId: string
}

export default function RebarDrilldown({ projectId }: RebarDrilldownProps) {
  const navigate = useNavigate()
  const [drawings, setDrawings] = useState<DrawingOption[]>([])
  const [selectedId, setSelectedId] = useState<string>('')
  const [listLoading, setListLoading] = useState(true)
  const [calc, setCalc] = useState<RebarCalcResult | null>(null)
  const [calcLoading, setCalcLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setListLoading(true)
    listDrawings({ project_id: projectId, limit: 200 })
      .then((res: unknown) => {
        if (cancelled) return
        const list = isListDrawingsResponse(res) ? res.items ?? [] : []
        setDrawings(list)
      })
      .finally(() => {
        if (!cancelled) setListLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  useEffect(() => {
    if (!selectedId) {
      setCalc(null)
      return
    }
    let cancelled = false
    setCalcLoading(true)
    getEconomicCalc(selectedId)
      .then((res: unknown) => {
        if (!cancelled) setCalc(res as RebarCalcResult)
      })
      .finally(() => {
        if (!cancelled) setCalcLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  return (
    <Card
      style={{ marginTop: 16 }}
      title={
        <Space>
          <CalculatorOutlined />
          钢筋翻样明细（下钻）
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <Typography.Text>图纸：</Typography.Text>
        <Select
          style={{ width: 320 }}
          loading={listLoading}
          placeholder="选择本项目图纸查看翻样结果"
          value={selectedId || undefined}
          onChange={setSelectedId}
          options={drawings.map((d) => ({ label: `${d.drawing_no} ${d.title}`, value: d.id }))}
          notFoundContent={listLoading ? <Spin size="small" /> : '本项目暂无图纸'}
        />
        {selectedId && (
          <Button size="small" onClick={() => navigate(`/drawings/${selectedId}`)}>
            打开图纸详情录入/重算
          </Button>
        )}
      </Space>

      {!selectedId && <Empty description="选择图纸查看该图钢筋翻样结果" />}

      {selectedId && calcLoading && (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>
      )}

      {selectedId && !calcLoading && calc?.exists === false && (
        <Alert
          type="info"
          showIcon
          message="该图纸暂无翻样计算结果"
          action={
            <Button size="small" onClick={() => navigate(`/drawings/${selectedId}`)}>
              去录入
            </Button>
          }
        />
      )}

      {selectedId && !calcLoading && calc && calc.exists !== false && (
        <Row gutter={16}>
          <Col span={6}>
            <Statistic title="钢筋总量" value={calc.total_steel_kg ?? 0} suffix="kg" precision={2} />
          </Col>
          <Col span={6}>
            <Statistic
              title="优化节约"
              value={calc.saving_kg ?? 0}
              suffix="kg"
              precision={2}
              valueStyle={{ color: '#3f8600' }}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="节约金额"
              value={calc.saving_yuan ?? 0}
              suffix="元"
              precision={2}
              valueStyle={{ color: '#3f8600' }}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="下料废料率"
              value={((calc.optimized_waste_rate ?? 0) * 100).toFixed(1)}
              suffix="%"
            />
          </Col>
        </Row>
      )}
    </Card>
  )
}
