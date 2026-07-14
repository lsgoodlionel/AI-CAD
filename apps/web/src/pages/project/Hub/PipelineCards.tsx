/**
 * 5 张流水线卡片：图纸 / 审查 / 建模 / 算量 / 创效
 * 响应式布局：大屏 3 列、平板 2 列、手机 1 列
 */
import { Col, Row } from 'antd'
import DrawingsCard from './cards/DrawingsCard'
import ReviewCard from './cards/ReviewCard'
import ModelCard from './cards/ModelCard'
import QuantityCard from './cards/QuantityCard'
import IncentiveCard from './cards/IncentiveCard'
import type { ProjectDashboardData } from './types'

interface PipelineCardsProps {
  projectId: string
  data: ProjectDashboardData
}

const CARD_COL_PROPS = { xs: 24, sm: 12, lg: 8 } as const

export default function PipelineCards({ projectId, data }: PipelineCardsProps) {
  const publishedCnt =
    data.drawings_by_status.find((row) => row.status === 'published')?.cnt ?? 0

  return (
    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
      <Col {...CARD_COL_PROPS}>
        <DrawingsCard drawingsByStatus={data.drawings_by_status} />
      </Col>
      <Col {...CARD_COL_PROPS}>
        <ReviewCard aiQuality={data.ai_quality} />
      </Col>
      <Col {...CARD_COL_PROPS}>
        <ModelCard projectId={projectId} publishedDrawingCount={publishedCnt} />
      </Col>
      <Col {...CARD_COL_PROPS}>
        <QuantityCard projectId={projectId} />
      </Col>
      <Col {...CARD_COL_PROPS}>
        <IncentiveCard
          proposalFunnel={data.proposal_funnel}
          annualSavingYuan={data.annual_saving_yuan}
          kpiRedFlag={data.kpi_red_flag}
        />
      </Col>
    </Row>
  )
}
