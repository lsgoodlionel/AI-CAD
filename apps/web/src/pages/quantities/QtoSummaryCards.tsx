/**
 * 算量中心顶部汇总卡：混凝土净体积/模板面积/钢筋量 + 构件覆盖率 + 分类型明细。
 * 同一口径同时喂给项目/楼层/单体三级下钻表（FloorBreakdownTable），本卡只展示项目级合计。
 */
import { Alert, Card, Col, Row, Statistic, Table } from 'antd'
import type { QtoSummary } from '@/services/quantities'

interface QtoSummaryCardsProps {
  summary: QtoSummary
}

interface ByTypeRow {
  key: string
  elementType: string
  count: number
  grossM3: number
  netM3: number
  formworkM2: number
}

const BY_TYPE_COLUMNS = [
  { title: '构件类型', dataIndex: 'elementType', key: 'elementType' },
  { title: '数量', dataIndex: 'count', key: 'count', width: 90 },
  { title: '毛体积(m³)', dataIndex: 'grossM3', key: 'grossM3', width: 120 },
  { title: '净体积(m³)', dataIndex: 'netM3', key: 'netM3', width: 120 },
  { title: '模板接触面积(m²)', dataIndex: 'formworkM2', key: 'formworkM2', width: 150 },
]

export default function QtoSummaryCards({ summary }: QtoSummaryCardsProps) {
  const byTypeRows: ByTypeRow[] = Object.entries(summary.by_type).map(([elementType, bucket]) => ({
    key: elementType,
    elementType,
    count: bucket.count,
    grossM3: bucket.gross_m3,
    netM3: bucket.net_m3,
    formworkM2: bucket.formwork_contact_m2,
  }))

  return (
    <>
      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="混凝土净体积" value={summary.concrete.net_m3} suffix="m³" precision={2} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="混凝土毛体积" value={summary.concrete.gross_m3} suffix="m³" precision={2} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="模板接触面积" value={summary.formwork.contact_m2} suffix="m²" precision={2} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            {summary.rebar?.missing === false ? (
              <Statistic title="钢筋量" value={summary.rebar.total_t ?? 0} suffix="t" precision={4} />
            ) : (
              <Statistic title="钢筋量" value="待翻样" />
            )}
          </Card>
        </Col>
      </Row>

      {summary.uncovered_count > 0 && (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message={`${summary.uncovered_count} 个构件未纳入算量（缺量集或几何提取失败），已如实排除，不静默计入`}
        />
      )}

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={8}>
          <Statistic title="构件总数" value={summary.element_count} />
        </Col>
        <Col span={8}>
          <Statistic title="实测构件数" value={summary.measured_count} valueStyle={{ color: '#3f8600' }} />
        </Col>
        <Col span={8}>
          <Statistic title="估算构件数" value={summary.estimated_count} valueStyle={{ color: '#d48806' }} />
        </Col>
      </Row>

      <Table
        style={{ marginTop: 16 }}
        size="small"
        pagination={false}
        columns={BY_TYPE_COLUMNS}
        dataSource={byTypeRows}
        title={() => '分构件类型明细'}
      />
    </>
  )
}
