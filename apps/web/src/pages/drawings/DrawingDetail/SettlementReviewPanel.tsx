import { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Alert, message,
  Divider, Table, Upload, Tag, Popconfirm,
} from 'antd'
import { UploadOutlined, PlusOutlined, CheckOutlined, CloseOutlined, DownloadOutlined } from '@ant-design/icons'
import {
  getSettlementReview, submitSettlementNodes, uploadQuotaSheet,
  approveSettlementReview, rejectSettlementReview,
} from '@/services/drawings'
import HelpTip from '@/components/HelpTip'

const PM_ROLES = ['project_manager', 'group_admin', 'group_chief_engineer']

interface SettlementNode {
  node_name: string
  description: string
  amount?: number
}

interface ReviewData {
  settlement_review: {
    id: string
    settlement_nodes: SettlementNode[]
    notes: string
    material_signed_at: string | null
  } | null
  quota_sheet_uploaded: boolean
  quota_download_url: string | null
}

interface Props {
  drawingId: string
  userRole: string
  onRefresh: () => void
}

export default function SettlementReviewPanel({ drawingId, userRole, onRefresh }: Props) {
  const [data, setData] = useState<ReviewData | null>(null)
  const [loading, setLoading] = useState(false)
  const [nodes, setNodes] = useState<SettlementNode[]>([{ node_name: '', description: '', amount: undefined }])
  const [form] = Form.useForm()

  const canOperate = PM_ROLES.includes(userRole)

  const fetchData = async () => {
    try {
      setData(await getSettlementReview(drawingId))
    } catch {
      setData(null)
    }
  }

  useEffect(() => { fetchData() }, [drawingId])

  useEffect(() => {
    if (data?.settlement_review?.settlement_nodes?.length) {
      setNodes(data.settlement_review.settlement_nodes)
      form.setFieldsValue({ notes: data.settlement_review.notes })
    }
  }, [data])

  const handleSubmitNodes = async () => {
    const values = await form.validateFields()
    const valid = nodes.filter(n => n.node_name.trim())
    setLoading(true)
    try {
      await submitSettlementNodes(drawingId, { settlement_nodes: valid, notes: values.notes ?? '' })
      message.success('结算节点已保存')
      fetchData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败')
    } finally {
      setLoading(false)
    }
  }

  const handleQuotaUpload = async (file: File) => {
    setLoading(true)
    try {
      await uploadQuotaSheet(drawingId, file)
      message.success('限额领料单已上传，发布约束已解锁')
      fetchData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '上传失败')
    } finally {
      setLoading(false)
    }
    return false
  }

  const handleApprove = async () => {
    setLoading(true)
    try {
      await approveSettlementReview(drawingId)
      message.success('三审通过，图纸已发布至班组')
      onRefresh()
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      if (detail?.code === 'QUOTA_SHEET_MISSING') {
        message.warning('《限额领料单》尚未上传，请先上传后再发布')
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
      await rejectSettlementReview(drawingId, '驳回')
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
        message="三审（结算合规化）由项目经理 / 物资经理负责，您当前角色无操作权限"
      />
    )
  }

  const nodeColumns = [
    { title: '节点名称', dataIndex: 'node_name' },
    { title: '说明', dataIndex: 'description', ellipsis: true },
    { title: '金额（元）', dataIndex: 'amount', render: (v?: number) => v !== null && v !== undefined ? v.toLocaleString() : '—' },
  ]

  return (
    <Card
      title={
        <>
          三审 — 结算合规化
          <HelpTip
            content="配置结算节点并上传《限额领料单》，两者是发布图纸至班组的硬约束——领料单未上传，发布将被系统拒绝。"
            anchor=""
          />
        </>
      }
      size="small"
    >
      {/* 限额领料单状态 */}
      {data?.quota_sheet_uploaded ? (
        <Alert
          type="success" showIcon
          style={{ marginBottom: 12 }}
          message={
            <Space>
              <span>《限额领料单》已上传，发布约束已解锁</span>
              {data.quota_download_url && (
                <a href={data.quota_download_url} target="_blank" rel="noreferrer">
                  <DownloadOutlined /> 下载
                </a>
              )}
            </Space>
          }
        />
      ) : (
        <Alert
          type="error" showIcon
          message="《限额领料单》尚未上传 — 上传后方可发布图纸"
          style={{ marginBottom: 12 }}
        />
      )}

      {/* 结算节点 */}
      {data?.settlement_review?.settlement_nodes?.length ? (
        <Table
          size="small"
          dataSource={data.settlement_review.settlement_nodes}
          columns={nodeColumns}
          rowKey="node_name"
          pagination={false}
          style={{ marginBottom: 12 }}
        />
      ) : (
        <Alert type="info" message="尚未配置结算节点" style={{ marginBottom: 12 }} />
      )}

      {/* 结算节点编辑 */}
      <Form form={form} layout="vertical">
        {nodes.map((node, idx) => (
          <Space key={idx} align="start" style={{ display: 'flex', marginBottom: 8 }}>
            <Input
              placeholder="节点名称"
              value={node.node_name}
              style={{ width: 140 }}
              onChange={e => setNodes(nodes.map((n, i) => i === idx ? { ...n, node_name: e.target.value } : n))}
            />
            <Input
              placeholder="说明"
              value={node.description}
              style={{ width: 220 }}
              onChange={e => setNodes(nodes.map((n, i) => i === idx ? { ...n, description: e.target.value } : n))}
            />
            <InputNumber
              placeholder="金额（元）"
              value={node.amount}
              style={{ width: 140 }}
              onChange={v => setNodes(nodes.map((n, i) => i === idx ? { ...n, amount: v ?? undefined } : n))}
            />
            {nodes.length > 1 && (
              <Button
                size="small" type="link" danger
                onClick={() => setNodes(nodes.filter((_, i) => i !== idx))}
              >
                删除
              </Button>
            )}
          </Space>
        ))}
        <Button
          icon={<PlusOutlined />}
          size="small"
          onClick={() => setNodes([...nodes, { node_name: '', description: '', amount: undefined }])}
          style={{ marginBottom: 12 }}
        >
          添加结算节点
        </Button>
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Button loading={loading} onClick={handleSubmitNodes}>保存结算节点</Button>
      </Form>

      <Divider style={{ margin: '12px 0' }} />

      {/* 限额领料单上传 */}
      <Space style={{ marginBottom: 12 }}>
        <Upload
          accept=".pdf"
          maxCount={1}
          showUploadList={false}
          beforeUpload={(file) => { handleQuotaUpload(file); return false }}
        >
          <Button icon={<UploadOutlined />} loading={loading}>
            {data?.quota_sheet_uploaded ? '重新上传限额领料单' : '上传限额领料单 PDF'}
          </Button>
        </Upload>
        {data?.quota_sheet_uploaded && <Tag color="green">已上传</Tag>}
      </Space>

      <Divider style={{ margin: '12px 0' }} />
      <Space>
        <Popconfirm
          title="确认通过三审并发布图纸"
          description={!data?.quota_sheet_uploaded ? '⚠️ 限额领料单未上传，发布将被拒绝' : '图纸将发布至班组，确认继续？'}
          onConfirm={handleApprove}
        >
          <Button type="primary" icon={<CheckOutlined />} loading={loading}>
            通过三审 → 发布图纸
          </Button>
        </Popconfirm>
        <Popconfirm title="确认驳回图纸？" onConfirm={handleReject}>
          <Button danger icon={<CloseOutlined />} loading={loading}>驳回</Button>
        </Popconfirm>
      </Space>
    </Card>
  )
}
