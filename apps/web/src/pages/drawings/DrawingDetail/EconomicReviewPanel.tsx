import { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Alert, message,
  Divider, Table, Select, Tag, Popconfirm, Modal,
} from 'antd'
import { PlusOutlined, CheckOutlined, CloseOutlined, EditOutlined } from '@ant-design/icons'
import {
  getEconomicReview, submitEconomicAlternatives, signEconomicReview,
  approveEconomicReview, rejectEconomicReview,
} from '@/services/drawings'
import HelpTip from '@/components/HelpTip'

const ECONOMIST_ROLES = ['economist', 'group_admin', 'group_commercial_director']

interface Alternative {
  option_id: string
  description: string
  cost_est: number
  notes: string
}

interface ReviewData {
  id: string
  alternatives: Alternative[]
  selected_option: string | null
  total_saving_est: number | null
  economist_signed: boolean
  economist_signed_at: string | null
  notes: string
}

interface Props {
  drawingId: string
  userRole: string
  onRefresh: () => void
}

export default function EconomicReviewPanel({ drawingId, userRole, onRefresh }: Props) {
  const [review, setReview] = useState<ReviewData | null>(null)
  const [loading, setLoading] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [form] = Form.useForm()
  const [alts, setAlts] = useState<Alternative[]>([{ option_id: 'A', description: '', cost_est: 0, notes: '' }])

  const canOperate = ECONOMIST_ROLES.includes(userRole)

  const fetchReview = async () => {
    try {
      const data = await getEconomicReview(drawingId)
      setReview(data)
    } catch {
      setReview(null)
    }
  }

  useEffect(() => { fetchReview() }, [drawingId])

  const handleSubmitAlts = async () => {
    const values = await form.validateFields()
    if (alts.length < 2) { message.error('请至少填写 2 种方案'); return }
    setLoading(true)
    try {
      await submitEconomicAlternatives(drawingId, {
        alternatives: alts,
        selected_option: values.selected_option,
        total_saving_est: values.total_saving_est,
        notes: values.notes,
      })
      message.success('方案对比表已提交')
      setEditOpen(false)
      fetchReview()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '提交失败')
    } finally {
      setLoading(false)
    }
  }

  const handleSign = async () => {
    setLoading(true)
    try {
      await signEconomicReview(drawingId)
      message.success('签字成功，图纸已解锁，可进入三审')
      fetchReview()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '签字失败')
    } finally {
      setLoading(false)
    }
  }

  const handleApprove = async () => {
    setLoading(true)
    try {
      await approveEconomicReview(drawingId)
      message.success('二审通过，已进入三审')
      onRefresh()
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      if (detail?.code === 'ECONOMIC_REVIEW_NOT_SIGNED') {
        message.warning('经济师尚未完成在线签字，请先签字后再通过审核')
      } else {
        message.error(typeof detail === 'string' ? detail : detail?.message ?? '操作失败')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleReject = async () => {
    setLoading(true)
    try {
      await rejectEconomicReview(drawingId, '驳回')
      message.success('已驳回，图纸退回草稿')
      onRefresh()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '操作失败')
    } finally {
      setLoading(false)
    }
  }

  if (!canOperate) {
    return (
      <Alert
        type="info"
        showIcon
        message="二审（经济最优化）由经济师负责，您当前角色无操作权限"
      />
    )
  }

  const altColumns = [
    { title: '方案', dataIndex: 'option_id', width: 60 },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '预估成本（元）', dataIndex: 'cost_est', render: (v: number) => v.toLocaleString() },
    { title: '备注', dataIndex: 'notes', ellipsis: true },
  ]

  return (
    <Card
      title={
        <>
          二审 — 经济最优化
          <HelpTip
            content="至少提交 2 种方案对比，经济师在线签字确认最优方案后方可解锁进入三审——签字是系统硬约束节点，未签字无法通过。"
            anchor=""
          />
        </>
      }
      size="small"
    >
      {/* 签字状态提示 */}
      {review?.economist_signed ? (
        <Alert
          type="success" showIcon
          message={`经济师已于 ${new Date(review.economist_signed_at!).toLocaleString('zh-CN')} 完成在线签字`}
          style={{ marginBottom: 12 }}
        />
      ) : (
        <Alert
          type="warning" showIcon
          message="经济师尚未完成在线签字 — 签字后方可推进至三审"
          style={{ marginBottom: 12 }}
        />
      )}

      {/* 方案对比表 */}
      {review ? (
        <>
          <Space style={{ marginBottom: 8 }}>
            <span>已选方案：</span>
            {review.selected_option
              ? <Tag color="blue">{review.selected_option}</Tag>
              : <Tag>未选定</Tag>}
            {review.total_saving_est !== null && review.total_saving_est !== undefined && (
              <span>预估节约：¥{review.total_saving_est.toLocaleString()}</span>
            )}
            {!review.economist_signed && (
              <Button size="small" icon={<EditOutlined />} onClick={() => {
                setAlts(review.alternatives)
                form.setFieldsValue({
                  selected_option: review.selected_option,
                  total_saving_est: review.total_saving_est,
                  notes: review.notes,
                })
                setEditOpen(true)
              }}>
                修改方案
              </Button>
            )}
          </Space>
          <Table
            size="small"
            dataSource={review.alternatives}
            columns={altColumns}
            rowKey="option_id"
            pagination={false}
            rowClassName={(r) => r.option_id === review.selected_option ? 'ant-table-row-selected' : ''}
          />
        </>
      ) : (
        <Alert type="info" message="尚未提交方案对比表" style={{ marginBottom: 8 }} />
      )}

      <Divider style={{ margin: '12px 0' }} />
      <Space wrap>
        {!review && (
          <Button icon={<PlusOutlined />} onClick={() => setEditOpen(true)}>
            提交方案对比表
          </Button>
        )}
        {review && !review.economist_signed && (
          <Popconfirm
            title="确认在线签字"
            description="签字后方案将锁定，无法再修改，确认继续？"
            onConfirm={handleSign}
          >
            <Button type="primary" loading={loading}>经济师在线签字</Button>
          </Popconfirm>
        )}
        {review?.economist_signed && (
          <>
            <Button type="primary" icon={<CheckOutlined />} loading={loading} onClick={handleApprove}>
              通过二审 → 进入三审
            </Button>
            <Button danger icon={<CloseOutlined />} loading={loading} onClick={handleReject}>
              驳回
            </Button>
          </>
        )}
      </Space>

      {/* 方案编辑 Modal */}
      <Modal
        title="提交方案对比表"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleSubmitAlts}
        confirmLoading={loading}
        width={680}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          {/* 方案动态行 */}
          {alts.map((alt, idx) => (
            <Card
              key={alt.option_id}
              size="small"
              title={`方案 ${alt.option_id}`}
              style={{ marginBottom: 12 }}
              extra={
                alts.length > 2 && (
                  <Button
                    size="small" danger type="link"
                    onClick={() => setAlts(alts.filter((_, i) => i !== idx))}
                  >
                    删除
                  </Button>
                )
              }
            >
              <Space direction="vertical" style={{ width: '100%' }}>
                <Input
                  placeholder="方案描述"
                  value={alt.description}
                  onChange={e => setAlts(alts.map((a, i) => i === idx ? { ...a, description: e.target.value } : a))}
                />
                <InputNumber
                  style={{ width: '100%' }}
                  placeholder="预估成本（元）"
                  value={alt.cost_est}
                  onChange={v => setAlts(alts.map((a, i) => i === idx ? { ...a, cost_est: v ?? 0 } : a))}
                />
                <Input
                  placeholder="备注（可选）"
                  value={alt.notes}
                  onChange={e => setAlts(alts.map((a, i) => i === idx ? { ...a, notes: e.target.value } : a))}
                />
              </Space>
            </Card>
          ))}
          <Button
            block
            icon={<PlusOutlined />}
            onClick={() => {
              const ids = ['A', 'B', 'C', 'D']
              const next = ids[alts.length] ?? `方案${alts.length + 1}`
              setAlts([...alts, { option_id: next, description: '', cost_est: 0, notes: '' }])
            }}
          >
            添加方案
          </Button>
          <Divider />
          <Form.Item name="selected_option" label="经济最优方案">
            <Select
              options={alts.map(a => ({ label: `方案 ${a.option_id}`, value: a.option_id }))}
              placeholder="选定经济最优方案（签字前必填）"
            />
          </Form.Item>
          <Form.Item name="total_saving_est" label="预估节约金额（元）">
            <InputNumber style={{ width: '100%' }} min={0} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
