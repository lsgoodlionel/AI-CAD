/**
 * 集团级数据看板（仅 group_admin 可见）
 * 年度创效 / 提案漏斗 / 图纸覆盖率 / KPI预警 / LLM成本
 *
 * Phase D D-15：作为「数据看板」统一入口（../index.tsx）的一个视图区块被嵌入，
 * 不再拥有独立页面标题/外层 padding（由父组件统一提供）。
 */
import { useEffect, useState } from 'react'
import {
  Row, Col, Card, Statistic, Table, Tag, Progress,
  Alert, Spin, Space, Badge, Typography,
} from 'antd'
import {
  TrophyOutlined, FileTextOutlined, RobotOutlined,
  WarningOutlined, DollarOutlined, BookOutlined,
} from '@ant-design/icons'
import { getGroupDashboard } from '@/services/dashboard'

const { Text } = Typography

const PROPOSAL_STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft:         { label: '草稿',   color: 'default' },
  calculating:   { label: '测算中', color: 'processing' },
  pending_sign:  { label: '待签字', color: 'warning' },
  public_notice: { label: '公示中', color: 'blue' },
  distributing:  { label: '分配中', color: 'purple' },
  approved:      { label: '已审批', color: 'success' },
  paid:          { label: '已兑现', color: 'green' },
  rejected:      { label: '已驳回', color: 'error' },
}

const DRAWING_STATUS_MAP: Record<string, string> = {
  draft: '草稿', ai_reviewing: 'AI审图中', ai_done: '审图完成',
  technical_review: '一审', economic_review: '二审',
  settlement_review: '三审', published: '已发布', rejected: '已驳回',
}

export default function GroupDashboard() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getGroupDashboard().then(setData).finally(() => setLoading(false))
  }, [])

  if (loading) return <Spin style={{ display: 'block', marginTop: 80 }} />
  if (!data) return <Alert type="error" message="加载失败" />

  const totalProposals = data.proposal_funnel.reduce((s: number, r: any) => s + Number(r.cnt), 0)
  const paidProposals = data.proposal_funnel.find((r: any) => r.status === 'paid')?.cnt ?? 0

  return (
    <div>
      <Text type="secondary" style={{ display: 'block', marginBottom: 16, fontSize: 13 }}>
        {new Date(data.generated_at).toLocaleString('zh-CN')} 生成
      </Text>

      {/* KPI 预警横幅 */}
      {data.kpi_warnings.length > 0 && (
        <Alert
          type="error"
          showIcon
          icon={<WarningOutlined />}
          style={{ marginBottom: 16 }}
          message={`${data.kpi_warnings.length} 个项目年度创效额不足 50 万，触发 KPI 红线`}
          description={
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {data.kpi_warnings.map((w: any) => (
                <li key={w.id}>
                  {w.name} — 年产值 {(w.annual_output / 1e8).toFixed(1)} 亿，
                  年度创效 {(w.year_saving / 10000).toFixed(1)} 万元
                </li>
              ))}
            </ul>
          }
        />
      )}

      {/* 核心指标卡 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title={<Space><TrophyOutlined />年度创效总额</Space>}
              value={(data.annual_saving_yuan / 10000).toFixed(1)}
              suffix="万元"
              valueStyle={{ color: '#3f8600', fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={<Space><FileTextOutlined />图纸总数</Space>}
              value={data.drawing_overview.total}
              suffix="张"
            />
            <Progress
              percent={Math.round(data.drawing_overview.ai_coverage_rate * 100)}
              size="small"
              style={{ marginTop: 8 }}
              format={p => `AI覆盖 ${p}%`}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="一审通过率"
              value={Math.round(data.review_stats.tech_pass_rate * 100)}
              suffix="%"
              valueStyle={{ color: data.review_stats.tech_pass_rate >= 0.8 ? '#3f8600' : '#cf1322' }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              二审签字率 {Math.round(data.review_stats.econ_sign_rate * 100)}%
            </Text>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={<Space><BookOutlined />规范知识库</Space>}
              value={data.regulation_stats.book_count ?? 0}
              suffix="本"
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {data.regulation_stats.article_count ?? 0} 条条文，
              {data.regulation_stats.vectorized_count ?? 0} 条已向量化
            </Text>
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        {/* 提案漏斗 */}
        <Col span={10}>
          <Card title={<Space><TrophyOutlined />创效提案漏斗</Space>} style={{ height: '100%' }}>
            <Table
              size="small"
              pagination={false}
              dataSource={data.proposal_funnel.map((r: any, i: number) => ({ ...r, key: i }))}
              columns={[
                {
                  title: '状态',
                  dataIndex: 'status',
                  render: (v: string) => {
                    const cfg = PROPOSAL_STATUS_MAP[v] ?? { label: v, color: 'default' }
                    return <Tag color={cfg.color}>{cfg.label}</Tag>
                  },
                },
                { title: '数量', dataIndex: 'cnt', width: 60 },
                {
                  title: '节约额（万元）',
                  dataIndex: 'total_saving',
                  render: (v: number) => v > 0 ? (v / 10000).toFixed(1) : '—',
                },
                {
                  title: '占比',
                  dataIndex: 'cnt',
                  render: (v: number) => (
                    <Progress
                      percent={totalProposals ? Math.round((v / totalProposals) * 100) : 0}
                      size="small"
                      showInfo={false}
                    />
                  ),
                },
              ]}
              summary={() => (
                <Table.Summary.Row>
                  <Table.Summary.Cell index={0}><Text strong>合计</Text></Table.Summary.Cell>
                  <Table.Summary.Cell index={1}><Text strong>{totalProposals}</Text></Table.Summary.Cell>
                  <Table.Summary.Cell index={2} colSpan={2}>
                    <Text strong>已兑现 {paidProposals} 件</Text>
                  </Table.Summary.Cell>
                </Table.Summary.Row>
              )}
            />
          </Card>
        </Col>

        {/* 图纸状态分布 */}
        <Col span={7}>
          <Card title={<Space><FileTextOutlined />图纸状态分布</Space>} style={{ height: '100%' }}>
            {data.drawing_overview.by_status.map((r: any) => (
              <div key={r.status} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <Text>{DRAWING_STATUS_MAP[r.status] ?? r.status}</Text>
                <Space>
                  <Badge count={r.cnt} showZero style={{ backgroundColor: r.status === 'published' ? '#52c41a' : '#1677ff' }} />
                  {r.ai_done_cnt > 0 && (
                    <Tag color="green" style={{ fontSize: 11 }}>AI✓ {r.ai_done_cnt}</Tag>
                  )}
                </Space>
              </div>
            ))}
          </Card>
        </Col>

        {/* LLM 调用成本 */}
        <Col span={7}>
          <Card
            title={<Space><RobotOutlined /><DollarOutlined />LLM 调用成本（近 30 天）</Space>}
            style={{ height: '100%' }}
          >
            {data.llm_cost_30d.length === 0 ? (
              <Text type="secondary">暂无调用记录</Text>
            ) : (
              <Table
                size="small"
                pagination={false}
                dataSource={data.llm_cost_30d.slice(0, 8).map((r: any, i: number) => ({ ...r, key: i }))}
                columns={[
                  { title: '引擎', dataIndex: 'engine_name', ellipsis: true },
                  { title: '调用次数', dataIndex: 'call_count', width: 70 },
                  {
                    title: '费用($)',
                    dataIndex: 'total_cost_usd',
                    width: 80,
                    render: (v: number) => Number(v).toFixed(4),
                  },
                ]}
                summary={(rows) => {
                  const total = rows.reduce((s, r) => s + Number((r as any).total_cost_usd), 0)
                  return (
                    <Table.Summary.Row>
                      <Table.Summary.Cell index={0} colSpan={2}><Text strong>合计</Text></Table.Summary.Cell>
                      <Table.Summary.Cell index={2}><Text strong>${total.toFixed(4)}</Text></Table.Summary.Cell>
                    </Table.Summary.Row>
                  )
                }}
              />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
