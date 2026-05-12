import { useRef, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { ProTable } from '@ant-design/pro-components'
import type { ActionType, ProColumns } from '@ant-design/pro-components'
import {
  Button, Tag, Space, Modal, Form, Input, Select, InputNumber,
  Upload, message, Badge,
} from 'antd'
import {
  UploadOutlined, PlusOutlined, EyeOutlined,
} from '@ant-design/icons'
import { listDrawings, uploadDrawing } from '@/services/drawings'

const STATUS_MAP: Record<string, { color: string; text: string }> = {
  draft:              { color: 'default',    text: '草稿' },
  ai_reviewing:       { color: 'processing', text: 'AI 审图中' },
  ai_done:            { color: 'warning',    text: 'AI 完成' },
  technical_review:   { color: 'blue',       text: '一审中' },
  economic_review:    { color: 'purple',     text: '二审中' },
  settlement_review:  { color: 'orange',     text: '三审中' },
  published:          { color: 'success',    text: '已发布' },
  rejected:           { color: 'error',      text: '已驳回' },
}

const DISCIPLINE_OPTIONS = [
  { label: '结构', value: 'structure' },
  { label: '建筑', value: 'architecture' },
  { label: '机电', value: 'mep' },
  { label: '幕墙', value: 'curtain_wall' },
  { label: '精装', value: 'decoration' },
  { label: '其他', value: 'other' },
]

const DISCIPLINE_LABEL: Record<string, string> = Object.fromEntries(
  DISCIPLINE_OPTIONS.map(({ value, label }) => [value, label])
)

interface DrawingRow {
  id: string
  drawing_no: string
  title: string
  discipline: string
  version: string
  status: string
  estimated_impact?: number
  creator_name: string
  project_name: string
  updated_at: string
}

export default function DrawingList() {
  const actionRef = useRef<ActionType>()
  const navigate = useNavigate()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [form] = Form.useForm()
  const [uploading, setUploading] = useState(false)
  const [fileList, setFileList] = useState<any[]>([])

  const columns: ProColumns<DrawingRow>[] = [
    {
      title: '图纸编号',
      dataIndex: 'drawing_no',
      width: 140,
      copyable: true,
    },
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
    },
    {
      title: '专业',
      dataIndex: 'discipline',
      width: 80,
      render: (_, row) => DISCIPLINE_LABEL[row.discipline] ?? row.discipline,
      valueEnum: Object.fromEntries(DISCIPLINE_OPTIONS.map(({ value, label }) => [value, { text: label }])),
    },
    {
      title: '版次',
      dataIndex: 'version',
      width: 60,
      search: false,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (_, row) => {
        const s = STATUS_MAP[row.status] ?? { color: 'default', text: row.status }
        return <Badge status={s.color as any} text={s.text} />
      },
      valueEnum: Object.fromEntries(
        Object.entries(STATUS_MAP).map(([k, v]) => [k, { text: v.text }])
      ),
    },
    {
      title: '预估金额',
      dataIndex: 'estimated_impact',
      search: false,
      width: 120,
      render: (_, row) =>
        row.estimated_impact
          ? `¥${(row.estimated_impact / 10000).toFixed(1)}万`
          : '—',
    },
    {
      title: '创建人',
      dataIndex: 'creator_name',
      search: false,
      width: 90,
    },
    {
      title: '所属项目',
      dataIndex: 'project_name',
      search: false,
      ellipsis: true,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      search: false,
      width: 150,
      render: (_, row) => new Date(row.updated_at).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 80,
      search: false,
      render: (_, row) => (
        <Button
          type="link"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/drawings/${row.id}`)}
        >
          查看
        </Button>
      ),
    },
  ]

  const handleUpload = async () => {
    const values = await form.validateFields()
    if (!fileList[0]) {
      message.error('请选择图纸文件')
      return
    }
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('project_id', values.project_id)
      fd.append('drawing_no', values.drawing_no)
      fd.append('discipline', values.discipline)
      fd.append('version', values.version ?? 'A')
      fd.append('title', values.title ?? '')
      if (values.estimated_impact) {
        fd.append('estimated_impact', String(values.estimated_impact))
      }
      fd.append('file', fileList[0].originFileObj)
      await uploadDrawing(fd)
      message.success('图纸已上传，AI 审图任务已触发')
      setUploadOpen(false)
      form.resetFields()
      setFileList([])
      actionRef.current?.reload()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '上传失败')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <ProTable<DrawingRow>
        actionRef={actionRef}
        rowKey="id"
        headerTitle="图纸列表"
        columns={columns}
        request={async (params) => {
          const { current, pageSize, ...rest } = params
          const res = await listDrawings({
            ...rest,
            limit: pageSize,
            offset: ((current ?? 1) - 1) * (pageSize ?? 20),
          })
          return { data: res.items, total: res.total, success: true }
        }}
        pagination={{ pageSize: 20 }}
        toolBarRender={() => [
          <Button
            key="upload"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setUploadOpen(true)}
          >
            上传图纸
          </Button>,
        ]}
      />

      <Modal
        title="上传图纸"
        open={uploadOpen}
        onCancel={() => { setUploadOpen(false); form.resetFields(); setFileList([]) }}
        onOk={handleUpload}
        confirmLoading={uploading}
        width={560}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="project_id" label="项目 ID" rules={[{ required: true }]}>
            <Input placeholder="项目 UUID" />
          </Form.Item>
          <Form.Item name="drawing_no" label="图纸编号" rules={[{ required: true }]}>
            <Input placeholder="如 ST-2025-001" />
          </Form.Item>
          <Space style={{ width: '100%' }} size={12}>
            <Form.Item name="discipline" label="专业" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select options={DISCIPLINE_OPTIONS} />
            </Form.Item>
            <Form.Item name="version" label="版次" initialValue="A" style={{ width: 80 }}>
              <Input />
            </Form.Item>
          </Space>
          <Form.Item name="title" label="标题">
            <Input />
          </Form.Item>
          <Form.Item name="estimated_impact" label="预估影响金额（元）">
            <InputNumber style={{ width: '100%' }} min={0} step={10000} />
          </Form.Item>
          <Form.Item label="图纸文件" required>
            <Upload
              accept=".pdf,.dwg,.dxf,.ifc"
              maxCount={1}
              fileList={fileList}
              beforeUpload={() => false}
              onChange={({ fileList: fl }) => setFileList(fl)}
            >
              <Button icon={<UploadOutlined />}>选择文件（PDF / DWG / DXF / IFC，≤200MB）</Button>
            </Upload>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
