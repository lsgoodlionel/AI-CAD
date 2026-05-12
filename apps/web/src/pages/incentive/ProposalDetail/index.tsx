import { useEffect, useState } from 'react'
import { useParams, useNavigate, useModel } from '@umijs/max'
import {
  Card, Descriptions, Button, Spin, Space, Alert, Steps, Tag,
  Statistic, Row, Col, Form, Input, InputNumber, Popconfirm, message, Divider, Table,
} from 'antd'
import {
  ArrowLeftOutlined, CheckOutlined, CloseOutlined,
  SafetyCertificateOutlined, MoneyCollectOutlined, FilePdfOutlined,
} from '@ant-design/icons'
import {
  getProposal, calculateSaving, signProposal, distributeBonus, rejectProposal,
  getCertificateUrl,
} from '@/services/incentive'

const STATUS_STEPS = [
  { key: 'draft',         title: '提案提交' },
  { key: 'calculating',   title: '经济核算' },
  { key: 'pending_sign',  title: '多方签字' },
  { key: 'public_notice', title: '公示期' },
  { key: 'distributing',  title: '奖金分配' },
  { key: 'approved',      title: '审批完成' },
]
const STATUS_ORDER = STATUS_STEPS.map(s => s.key)

// 铁三角比例（前端展示用，与后端硬编码保持一致，不可编辑）
const IRON_TRIANGLE = { group: 20, team: 50, proposer: 30 }

export default function ProposalDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { initialState } = useModel('@@initialState')
  const currentUser = (initialState as any)?.currentUser
  const userRole: string = currentUser?.role ?? ''

  const [proposal, setProposal] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState(false)
  const [calcForm] = Form.useForm()
  const [distForm] = Form.useForm()
  const [rejectForm] = Form.useForm()
  const [teamRows, setTeamRows] = useState<{ display_name: string; amount: number }[]>([])

  const fetch = async () => {
    if (!id) return
    setLoading(true)
    try { setProposal(await getProposal(id)) }
    finally { setLoading(false) }
  }
  useEffect(() => { fetch() }, [id])

  if (loading) return <Spin style={{ display: 'block', marginTop: 80 }} />
  if (!proposal) return <Alert type="error" message="提案不存在" style={{ margin: 24 }} />

  const status: string = proposal.status
  const snapshot = proposal.cost_snapshot
  const currentStep = Math.max(STATUS_ORDER.indexOf(status), 0)
  const isRejected = status === 'rejected'

  // ── 操作 handlers ─────────────────────────────────────────

  const handleCalculate = async () => {
    const v = await calcForm.validateFields()
    setActing(true)
    try {
      const res = await calculateSaving(id!, { net_saving: v.net_saving, bonus_rate: v.bonus_rate ?? 0.15, notes: v.notes })
      message.success(`核算完成，奖励池 ¥${res.snapshot.bonus_pool.toLocaleString()}`)
      fetch()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '核算失败')
    } finally { setActing(false) }
  }

  const handleSign = async () => {
    setActing(true)
    try {
      const res = await signProposal(id!, '')
      message.success(res.all_signed ? '所有签字完成，已进入公示期' : '签字成功，等待其他角色签字')
      fetch()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '签字失败')
    } finally { setActing(false) }
  }

  const handleDistribute = async () => {
    setActing(true)
    try {
      const res = await distributeBonus(id!, teamRows.map((r, i) => ({ user_id: String(i), display_name: r.display_name, amount: r.amount })))
      message.success(`奖金分配完成 — 集团 ¥${res.group_amount.toLocaleString()} / 项目部 ¥${res.team_pool.toLocaleString()} / 提案人 ¥${res.proposer_amount.toLocaleString()}`)
      fetch()
    } catch (e: any) {
      const d = e?.response?.data?.detail
      message.error(typeof d === 'string' ? d : d?.message ?? '操作失败')
    } finally { setActing(false) }
  }

  const handleReject = async () => {
    const v = await rejectForm.validateFields()
    setActing(true)
    try {
      await rejectProposal(id!, v.reason)
      message.success('提案已驳回')
      fetch()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '操作失败')
    } finally { setActing(false) }
  }

  // ── 铁三角预览 ─────────────────────────────────────────────
  const bonusPool = snapshot?.bonus_pool ?? 0
  const TriangleCard = () => (
    <Card size="small" title="铁三角奖金分配（硬编码，不可调整）" style={{ marginTop: 16 }}>
      <Row gutter={24}>
        {[
          { label: '集团', key: 'group_amount', pct: IRON_TRIANGLE.group, color: '#1677ff' },
          { label: '项目部', key: 'team_pool', pct: IRON_TRIANGLE.team, color: '#52c41a' },
          { label: '提案人', key: 'proposer_amount', pct: IRON_TRIANGLE.proposer, color: '#fa8c16' },
        ].map(({ label, key, pct, color }) => (
          <Col span={8} key={key}>
            <Statistic
              title={<span style={{ color }}>{label}（{pct}%）</span>}
              value={snapshot?.[key] ?? bonusPool * pct / 100}
              prefix="¥"
              precision={2}
            />
          </Col>
        ))}
      </Row>
    </Card>
  )

  // ── 已签字列表 ─────────────────────────────────────────────
  const signedRoles = new Set((proposal.approvals ?? []).filter((a: any) => a.signed_at && a.role !== 'reject').map((a: any) => a.role))

  return (
    <div style={{ padding: 24, maxWidth: 900 }}>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/incentive')}>返回列表</Button>
      </Space>

      {/* 基本信息 */}
      <Card style={{ marginBottom: 16 }}>
        <Descriptions
          title={
            <Space>
              <Tag color={proposal.proposal_type === 'A' ? 'blue' : 'green'}>
                {proposal.proposal_type}类
              </Tag>
              {proposal.title}
            </Space>
          }
          column={3} size="small"
        >
          <Descriptions.Item label="提案人">{proposal.proposer_name ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="所属项目">{proposal.project_name ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="提交时间">{new Date(proposal.created_at).toLocaleString('zh-CN')}</Descriptions.Item>
          <Descriptions.Item label="提案人预估" span={1}>
            {proposal.raw_saving_est ? `¥${Number(proposal.raw_saving_est).toLocaleString()}` : '未填写'}
          </Descriptions.Item>
          <Descriptions.Item label="商务核算净节约" span={2}>
            {proposal.net_saving != null
              ? <strong style={{ color: '#389e0d', fontSize: 16 }}>¥{Number(proposal.net_saving).toLocaleString()}</strong>
              : '待核算'}
          </Descriptions.Item>
          <Descriptions.Item label="提案说明" span={3}>
            <div style={{ whiteSpace: 'pre-wrap' }}>{proposal.description}</div>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 状态步骤 */}
      <Card style={{ marginBottom: 16 }}>
        {isRejected ? (
          <Alert type="error" showIcon message={`提案已驳回 — ${(proposal.approvals ?? []).find((a: any) => a.role === 'reject')?.comment ?? ''}`} />
        ) : (
          <Steps
            current={currentStep}
            status={status === 'approved' || status === 'paid' ? 'finish' : 'process'}
            items={STATUS_STEPS.map((s, i) => ({
              title: s.title,
              status: i < currentStep ? 'finish' : i === currentStep ? 'process' : 'wait',
              description: s.key === 'public_notice' && proposal.notice_ends_at
                ? `公示截止：${new Date(proposal.notice_ends_at).toLocaleDateString('zh-CN')}`
                : undefined,
            }))}
            size="small"
          />
        )}
      </Card>

      {/* 铁三角预览（核算后显示） */}
      {snapshot && <TriangleCard />}

      <Divider />

      {/* ── 按状态显示操作面板 ── */}

      {/* 草稿：经济师发起核算 */}
      {status === 'draft' && ['economist','group_admin','group_commercial_director'].includes(userRole) && (
        <Card title="经济核算" size="small">
          <Form form={calcForm} layout="vertical">
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item name="net_saving" label="商务核算净节约额（元）" rules={[{ required: true }]}>
                  <InputNumber style={{ width: '100%' }} min={0.01} step={10000} prefix="¥" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="bonus_rate" label="奖励比例" initialValue={0.15}>
                  <InputNumber style={{ width: '100%' }} min={0.01} max={0.5} step={0.01}
                    formatter={v => `${((v as number) * 100).toFixed(0)}%`}
                    parser={v => Number(v?.replace('%', '')) / 100} />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item name="notes" label="核算备注">
              <Input.TextArea rows={2} />
            </Form.Item>
          </Form>
          <Button type="primary" icon={<MoneyCollectOutlined />} loading={acting} onClick={handleCalculate}>
            确认核算数据 → 进入签字
          </Button>
        </Card>
      )}

      {/* 待签字 */}
      {status === 'pending_sign' && (
        <Card title="多方签字确认" size="small">
          <Space direction="vertical" style={{ width: '100%' }}>
            {['project_manager','economist'].map(role => (
              <Alert
                key={role}
                type={signedRoles.has(role) ? 'success' : 'warning'}
                showIcon
                message={`${role === 'project_manager' ? '项目经理' : '经济师'}：${signedRoles.has(role) ? '已签字' : '待签字'}`}
              />
            ))}
            {!signedRoles.has(
              userRole === 'project_manager' ? 'project_manager'
              : ['economist','group_commercial_director'].includes(userRole) ? 'economist'
              : ''
            ) && (
              <Button type="primary" icon={<SafetyCertificateOutlined />} loading={acting} onClick={handleSign}>
                我确认签字
              </Button>
            )}
          </Space>
        </Card>
      )}

      {/* 公示期 */}
      {status === 'public_notice' && (
        <Alert
          type="info" showIcon
          message={`公示期中 — 截止 ${new Date(proposal.notice_ends_at).toLocaleString('zh-CN')}，公示期满后可发起奖金分配`}
        />
      )}

      {/* 奖金分配（公示期结束后） */}
      {status === 'public_notice' && ['group_admin','group_chief_engineer'].includes(userRole) && (
        <Card title="奖金分配" size="small" style={{ marginTop: 16 }}>
          <Alert type="warning" showIcon message="项目部内部分配明细（选填）" style={{ marginBottom: 12 }} />
          <Table
            size="small"
            dataSource={teamRows}
            rowKey={(_, i) => String(i)}
            columns={[
              { title: '姓名', dataIndex: 'display_name', render: (v, _, i) => (
                <Input value={v} onChange={e => setTeamRows(teamRows.map((r, ri) => ri === i ? { ...r, display_name: e.target.value } : r))} />
              )},
              { title: '金额（元）', dataIndex: 'amount', render: (v, _, i) => (
                <InputNumber value={v} min={0} onChange={n => setTeamRows(teamRows.map((r, ri) => ri === i ? { ...r, amount: n ?? 0 } : r))} />
              )},
              { title: '', render: (_, __, i) => <Button size="small" danger type="link" onClick={() => setTeamRows(teamRows.filter((_, ri) => ri !== i))}>删除</Button> },
            ]}
            footer={() => (
              <Button size="small" onClick={() => setTeamRows([...teamRows, { display_name: '', amount: 0 }])}>添加成员</Button>
            )}
            pagination={false}
          />
          <Divider style={{ margin: '12px 0' }} />
          <Popconfirm title="确认发起奖金分配？" description="操作不可撤销，将生成正式分配记录。" onConfirm={handleDistribute}>
            <Button type="primary" icon={<CheckOutlined />} loading={acting}>确认分配奖金</Button>
          </Popconfirm>
        </Card>
      )}

      {/* 终态：已审批/已兑现 */}
      {(status === 'approved' || status === 'paid') && proposal.distribution && (
        <Card
          title="奖金分配结果"
          size="small"
          extra={
            <Button
              type="primary"
              icon={<FilePdfOutlined />}
              onClick={() => window.open(getCertificateUrl(id!), '_blank')}
            >
              下载兑现凭证
            </Button>
          }
        >
          <Row gutter={24}>
            {[
              { label: '集团（20%）', value: proposal.distribution.group_amount, color: '#1677ff' },
              { label: '项目部（50%）', value: proposal.distribution.team_pool, color: '#52c41a' },
              { label: '提案人（30%）', value: proposal.distribution.proposer_amount, color: '#fa8c16' },
            ].map(({ label, value, color }) => (
              <Col span={8} key={label}>
                <Statistic title={<span style={{ color }}>{label}</span>} value={value} prefix="¥" precision={2} />
              </Col>
            ))}
          </Row>
        </Card>
      )}

      {/* 驳回操作（适用于非终态） */}
      {!['approved','paid','rejected'].includes(status) && ['project_manager','economist','group_admin','group_chief_engineer','group_commercial_director'].includes(userRole) && (
        <Card title="驳回提案" size="small" style={{ marginTop: 16 }} type="inner">
          <Form form={rejectForm} layout="inline">
            <Form.Item name="reason" rules={[{ required: true, min: 2 }]} style={{ flex: 1 }}>
              <Input placeholder="驳回原因（必填）" />
            </Form.Item>
            <Popconfirm title="确认驳回此提案？" onConfirm={handleReject}>
              <Button danger icon={<CloseOutlined />} loading={acting}>驳回</Button>
            </Popconfirm>
          </Form>
        </Card>
      )}
    </div>
  )
}
