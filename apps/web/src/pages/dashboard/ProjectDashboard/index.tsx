/**
 * 项目级数据看板（所有已登录用户可见）
 * 图纸流转 / AI 审图质量 / 提案漏斗 / 近期活动 / 管线待办建议
 *
 * Phase D D-15：作为「数据看板」统一入口（../index.tsx）的一个视图区块被嵌入，
 * 不再拥有独立页面标题/外层 padding（由父组件统一提供）。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from '@umijs/max'
import {
  Row, Col, Card, Statistic, Select, Table, Tag,
  Alert, Spin, Space, Timeline, Typography, Button,
} from 'antd'
import {
  FileTextOutlined, TrophyOutlined, RobotOutlined, WarningOutlined, BuildOutlined,
} from '@ant-design/icons'
import { getProjectDashboard } from '@/services/dashboard'
import { listProjects } from '@/services/projects'
import PipelineStatusPanel from '../PipelineStatusPanel'

const { Text } = Typography

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  draft:            { label: '草稿',     color: 'default' },
  ai_reviewing:     { label: 'AI审图中', color: 'processing' },
  ai_done:          { label: '审图完成', color: 'warning' },
  technical_review: { label: '一审',     color: 'blue' },
  economic_review:  { label: '二审',     color: 'purple' },
  settlement_review:{ label: '三审',     color: 'orange' },
  published:        { label: '已发布',   color: 'success' },
  rejected:         { label: '已驳回',   color: 'error' },
}

const PROPOSAL_STATUS: Record<string, string> = {
  draft: '草稿', calculating: '测算中', pending_sign: '待签字',
  public_notice: '公示中', distributing: '分配中',
  approved: '已审批', paid: '已兑现', rejected: '已驳回',
}

const DISCIPLINE_LABEL: Record<string, string> = {
  architecture: '建筑', structure: '结构', mep: '机电',
  decoration: '装修', general: '通用',
}

const ACTION_LABEL: Record<string, string> = {
  upload_drawing: '上传图纸', approve_technical: '一审通过',
  sign_economic: '经济师签字', publish_drawing: '发布图纸',
  submit_proposal: '提交提案', approve_proposal: '审批提案',
}

export default function ProjectDashboard() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([])
  const [projectId, setProjectId] = useState<string>('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    listProjects({ limit: 200 }).then((res: any) => {
      const list = (res.items ?? []).map((p: any) => ({ id: p.id, name: p.name }))
      setProjects(list)
      if (list.length > 0) setProjectId(list[0].id)
    })
  }, [])

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    getProjectDashboard(projectId).then(setData).finally(() => setLoading(false))
  }, [projectId])

  const totalDrawings = data?.drawings_by_status?.reduce((s: number, r: any) => s + Number(r.cnt), 0) ?? 0
  const publishedCnt = data?.drawings_by_status?.find((r: any) => r.status === 'published')?.cnt ?? 0
  const totalProposals = data?.proposal_funnel?.reduce((s: number, r: any) => s + Number(r.cnt), 0) ?? 0

  return (
    <div>
      <Space style={{ marginBottom: 24 }} size="large">
        <Text type="secondary">项目：</Text>
        <Select
          style={{ width: 240 }}
          value={projectId || undefined}
          placeholder="选择项目"
          onChange={setProjectId}
          options={projects.map(p => ({ label: p.name, value: p.id }))}
        />
        <Button
          icon={<BuildOutlined />}
          disabled={!projectId}
          onClick={() => navigate(`/model/${projectId}`)}
        >
          工程模型
        </Button>
      </Space>

      {loading && <Spin style={{ display: 'block', marginTop: 60 }} />}

      {!loading && data && (
        <>
          {/* KPI 红线 */}
          {data.kpi_red_flag && (
            <Alert
              type="error"
              showIcon
              icon={<WarningOutlined />}
              message="KPI 红线预警：年产值超 1 亿元项目，年度创效额不足 50 万，将影响年度评优"
              style={{ marginBottom: 16 }}
            />
          )}

          {/* 核心指标 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col span={6}>
              <Card>
                <Statistic
                  title="图纸总数"
                  value={totalDrawings}
                  suffix="张"
                  prefix={<FileTextOutlined />}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  已发布 {publishedCnt} 张（
                  {totalDrawings ? Math.round((publishedCnt / totalDrawings) * 100) : 0}%）
                </Text>
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="AI 已审图纸"
                  value={data.ai_quality?.reviewed_count ?? 0}
                  suffix="张"
                  prefix={<RobotOutlined />}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  平均 {Number(data.ai_quality?.avg_issues ?? 0).toFixed(1)} 个问题/张，
                  强条 {data.ai_quality?.total_critical ?? 0} 处
                </Text>
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="创效提案"
                  value={totalProposals}
                  suffix="件"
                  prefix={<TrophyOutlined />}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  已兑现节约额 {(data.annual_saving_yuan / 10000).toFixed(1)} 万元
                </Text>
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="含强条违规图纸"
                  value={data.ai_quality?.drawings_with_critical ?? 0}
                  suffix="张"
                  valueStyle={{
                    color: (data.ai_quality?.drawings_with_critical ?? 0) > 0 ? '#cf1322' : '#3f8600',
                  }}
                />
              </Card>
            </Col>
          </Row>

          {/* 管线待办建议（Phase D D-08 事件编排层 → D-15 接入看板） */}
          <PipelineStatusPanel projectId={projectId} />

          <Row gutter={16}>
            {/* 图纸状态 + 专业分布 */}
            <Col span={8}>
              <Card title="图纸流转状态" style={{ marginBottom: 16 }}>
                <Table
                  size="small"
                  pagination={false}
                  dataSource={data.drawings_by_status.map((r: any, i: number) => ({ ...r, key: i }))}
                  columns={[
                    {
                      title: '状态',
                      dataIndex: 'status',
                      render: (v: string) => {
                        const cfg = STATUS_LABEL[v] ?? { label: v, color: 'default' }
                        return <Tag color={cfg.color}>{cfg.label}</Tag>
                      },
                    },
                    { title: '数量', dataIndex: 'cnt', width: 60 },
                  ]}
                />
              </Card>
              <Card title="专业分布">
                <Table
                  size="small"
                  pagination={false}
                  dataSource={data.stage_distribution.map((r: any, i: number) => ({ ...r, key: i }))}
                  columns={[
                    {
                      title: '专业',
                      dataIndex: 'discipline',
                      render: (v: string) => DISCIPLINE_LABEL[v] ?? v,
                    },
                    { title: '总数', dataIndex: 'total_cnt', width: 50 },
                    { title: '已发布', dataIndex: 'published_cnt', width: 60 },
                    { title: '驳回', dataIndex: 'rejected_cnt', width: 50 },
                  ]}
                />
              </Card>
            </Col>

            {/* 提案漏斗 */}
            <Col span={7}>
              <Card title="创效提案漏斗" style={{ height: '100%' }}>
                {data.proposal_funnel.length === 0 ? (
                  <Text type="secondary">暂无提案</Text>
                ) : (
                  <Table
                    size="small"
                    pagination={false}
                    dataSource={data.proposal_funnel.map((r: any, i: number) => ({ ...r, key: i }))}
                    columns={[
                      {
                        title: '状态',
                        dataIndex: 'status',
                        render: (v: string) => (
                          <Tag>{PROPOSAL_STATUS[v] ?? v}</Tag>
                        ),
                      },
                      { title: '件数', dataIndex: 'cnt', width: 55 },
                      {
                        title: '节约额',
                        dataIndex: 'total_saving',
                        render: (v: number) =>
                          v > 0 ? `¥${(v / 10000).toFixed(1)}万` : '—',
                      },
                    ]}
                  />
                )}
              </Card>
            </Col>

            {/* 近期活动 */}
            <Col span={9}>
              <Card title="近期活动" style={{ height: '100%' }}>
                {data.recent_activity.length === 0 ? (
                  <Text type="secondary">暂无活动记录</Text>
                ) : (
                  <Timeline
                    items={data.recent_activity.map((r: any) => ({
                      children: (
                        <div>
                          <Text strong style={{ fontSize: 13 }}>
                            {ACTION_LABEL[r.action] ?? r.action}
                          </Text>
                          <br />
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {r.operator} · {new Date(r.created_at).toLocaleString('zh-CN')}
                          </Text>
                        </div>
                      ),
                    }))}
                  />
                )}
              </Card>
            </Col>
          </Row>
        </>
      )}

      {!loading && !data && projectId && (
        <Alert type="warning" message="暂无数据，请先创建图纸或提案" />
      )}
    </div>
  )
}
