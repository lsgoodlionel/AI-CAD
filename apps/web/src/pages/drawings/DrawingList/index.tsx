import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { ProTable } from '@ant-design/pro-components'
import type { ActionType, ProColumns, ProFormInstance } from '@ant-design/pro-components'
import {
  Button, Space, Modal, Form, Input, Select, InputNumber,
  Upload, Table, message, Badge,
} from 'antd'
import type { TableProps, UploadFile } from 'antd'
import type { PresetStatusColorType } from 'antd/es/_util/colors'
import {
  UploadOutlined, PlusOutlined, EyeOutlined, RobotOutlined, AppstoreOutlined, BuildOutlined,
} from '@ant-design/icons'
import {
  listDrawings, uploadDrawing, batchUploadDrawings, createReviewBatch,
} from '@/services/drawings'
import type { BatchUploadResult, CreateReviewBatchResult } from '@/services/drawings'
import { listProjects } from '@/services/projects'

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

const PRESET_BADGE_STATUSES: readonly PresetStatusColorType[] = [
  'success', 'processing', 'default', 'error', 'warning',
]

/** 预置状态走 status（processing 有动效），其余颜色走 color 渲染彩色圆点 */
function renderStatusBadge(color: string, text: string) {
  if ((PRESET_BADGE_STATUSES as readonly string[]).includes(color)) {
    return <Badge status={color as PresetStatusColorType} text={text} />
  }
  return <Badge color={color} text={text} />
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
  project_id: string
  project_name: string
  updated_at: string
}

interface ProjectOption {
  id: string
  name: string
  code?: string
}

/** 上传 Modal 中每个待上传文件的可编辑元数据行 */
interface UploadMetaRow {
  uid: string
  filename: string
  drawing_no: string
  discipline: string
  version: string
  title: string
}

/** 与后端 services/drawing_filename_parser.py 同规则的前端简版：图号首个匹配 */
const DRAWING_NO_RE = /[A-Za-z一-龥]{1,4}[-_ ]?\d{1,4}/

/** 文件名预解析：专业前缀 + 图号，解析不出的字段给安全默认值 */
function parseFilenameMeta(filename: string): Omit<UploadMetaRow, 'uid' | 'filename'> {
  const stem = filename.replace(/\.[^.]+$/, '')
  let discipline = 'other'
  if (/结施|GS/i.test(stem)) discipline = 'structure'
  else if (/建施|JS/i.test(stem)) discipline = 'architecture'
  else if (/水施|电施|暖施/.test(stem)) discipline = 'mep'
  else if (/装施/.test(stem)) discipline = 'decoration'
  const noMatch = stem.match(DRAWING_NO_RE)
  return {
    drawing_no: noMatch ? noMatch[0] : stem,
    discipline,
    version: 'A',
    title: stem,
  }
}

/** 从未知错误中提取后端 detail/error 文案 */
function extractErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const resp = (error as { response?: { data?: { detail?: string; error?: string } } }).response
    return resp?.data?.detail ?? resp?.data?.error ?? fallback
  }
  return fallback
}

export default function DrawingList() {
  const actionRef = useRef<ActionType>()
  const formRef = useRef<ProFormInstance>()
  const navigate = useNavigate()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [form] = Form.useForm()
  const [uploading, setUploading] = useState(false)
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [metaRows, setMetaRows] = useState<UploadMetaRow[]>([])
  const [selectedRows, setSelectedRows] = useState<DrawingRow[]>([])
  const [projects, setProjects] = useState<ProjectOption[]>([])

  useEffect(() => {
    listProjects({ limit: 200 }).then((res: { items?: ProjectOption[] }) =>
      setProjects(res.items ?? [])
    )
  }, [])

  const projectSelectOptions = projects.map((p) => ({
    label: `${p.name}${p.code ? ` (${p.code})` : ''}`,
    value: p.id,
  }))

  const columns: ProColumns<DrawingRow>[] = [
    {
      title: '所属项目',
      dataIndex: 'project_id',
      hideInTable: true,
      valueType: 'select',
      fieldProps: {
        showSearch: true,
        optionFilterProp: 'label',
        placeholder: '选择项目（整套审图需先选定）',
        options: projectSelectOptions,
      },
    },
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
        return renderStatusBadge(s.color, s.text)
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

  // ── 批量 AI 审图（选中 ≥1 张，须同项目）────────────────────
  const handleBatchReview = async () => {
    if (!selectedRows.length) return
    const projectIds = new Set(selectedRows.map((r) => r.project_id))
    if (projectIds.size > 1) {
      message.warning('所选图纸必须属于同一项目，请重新选择')
      return
    }
    try {
      const res: CreateReviewBatchResult = await createReviewBatch({
        project_id: selectedRows[0].project_id,
        drawing_ids: selectedRows.map((r) => r.id),
      })
      message.success(`套图审查任务已创建，共 ${res.total} 张图纸`)
      setSelectedRows([])
      navigate(`/drawings/review-batches/${res.batch_id}`)
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, '创建套图审查任务失败'))
    }
  }

  // ── 整套审图（需先在筛选里选定项目）────────────────────────
  const handleFullSetReview = () => {
    const projectId: string | undefined = formRef.current?.getFieldValue('project_id')
    if (!projectId) {
      message.warning('请先在筛选条件中选定项目，再发起整套审图')
      return
    }
    const project = projects.find((p) => p.id === projectId)
    Modal.confirm({
      title: '整套审图确认',
      content: `将对项目「${project?.name ?? projectId}」的全部可审图纸发起 AI 审图，确认继续？`,
      okText: '确认发起',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res: CreateReviewBatchResult = await createReviewBatch({ project_id: projectId })
          message.success(`整套审图任务已创建，共 ${res.total} 张图纸`)
          navigate(`/drawings/review-batches/${res.batch_id}`)
        } catch (e: unknown) {
          message.error(extractErrorMessage(e, '创建整套审图任务失败'))
        }
      },
    })
  }

  // ── 工程模型入口（需先在筛选里选定项目）────────────────────
  const handleOpenProjectModel = () => {
    const projectFilter: string | undefined = formRef.current?.getFieldValue('project_id')
    if (!projectFilter) {
      message.warning('请先选择项目')
      return
    }
    navigate(`/model/${projectFilter}`)
  }

  // ── 上传 Modal ─────────────────────────────────────────────
  const syncMetaRows = (fl: UploadFile[]) => {
    setFileList(fl)
    setMetaRows((prev) =>
      fl.map(
        (f) =>
          prev.find((r) => r.uid === f.uid) ?? {
            uid: f.uid,
            filename: f.name,
            ...parseFilenameMeta(f.name),
          }
      )
    )
  }

  const updateMetaRow = (uid: string, field: keyof UploadMetaRow, value: string) => {
    setMetaRows((prev) => prev.map((r) => (r.uid === uid ? { ...r, [field]: value } : r)))
  }

  const closeUploadModal = () => {
    setUploadOpen(false)
    form.resetFields()
    setFileList([])
    setMetaRows([])
  }

  const handleUpload = async () => {
    const values = await form.validateFields()
    if (!fileList.length) {
      message.error('请选择图纸文件')
      return
    }
    const missingNo = metaRows.find((r) => !r.drawing_no.trim())
    if (missingNo) {
      message.error(`请填写文件「${missingNo.filename}」的图号`)
      return
    }
    setUploading(true)
    try {
      if (fileList.length === 1) {
        // 单文件走原有单张接口保持兼容
        const row = metaRows[0]
        const file = fileList[0].originFileObj
        if (!file) {
          message.error('文件读取失败，请重新选择')
          return
        }
        const fd = new FormData()
        fd.append('project_id', values.project_id)
        fd.append('drawing_no', row.drawing_no.trim())
        fd.append('discipline', row.discipline)
        fd.append('version', row.version.trim() || 'A')
        fd.append('title', row.title)
        if (values.estimated_impact) {
          fd.append('estimated_impact', String(values.estimated_impact))
        }
        fd.append('file', file)
        await uploadDrawing(fd)
        message.success('图纸已上传，AI 审图任务已触发')
      } else {
        // 多文件组装 items_meta + files 走批量接口
        const fd = new FormData()
        fd.append('project_id', values.project_id)
        fd.append(
          'items_meta',
          JSON.stringify(
            metaRows.map(({ filename, drawing_no, discipline, version, title }) => ({
              filename,
              drawing_no: drawing_no.trim(),
              discipline,
              version: version.trim() || 'A',
              title,
            }))
          )
        )
        for (const f of fileList) {
          if (f.originFileObj) fd.append('files', f.originFileObj)
        }
        const res: BatchUploadResult = await batchUploadDrawings(fd)
        if (res.failed.length) {
          message.warning(
            `成功 ${res.created.length} 张，失败 ${res.failed.length} 张：` +
            res.failed.map((x) => `${x.filename}（${x.error}）`).join('、')
          )
        } else {
          message.success(`已上传 ${res.created.length} 张图纸，触发 ${res.review_triggered} 个 AI 审图任务`)
        }
      }
      closeUploadModal()
      actionRef.current?.reload()
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, '上传失败'))
    } finally {
      setUploading(false)
    }
  }

  const metaColumns: TableProps<UploadMetaRow>['columns'] = [
    { title: '文件名', dataIndex: 'filename', width: 180, ellipsis: true },
    {
      title: '图号',
      dataIndex: 'drawing_no',
      width: 130,
      render: (_, row) => (
        <Input
          size="small"
          value={row.drawing_no}
          onChange={(e) => updateMetaRow(row.uid, 'drawing_no', e.target.value)}
        />
      ),
    },
    {
      title: '专业',
      dataIndex: 'discipline',
      width: 110,
      render: (_, row) => (
        <Select
          size="small"
          style={{ width: '100%' }}
          options={DISCIPLINE_OPTIONS}
          value={row.discipline}
          onChange={(v: string) => updateMetaRow(row.uid, 'discipline', v)}
        />
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      width: 70,
      render: (_, row) => (
        <Input
          size="small"
          value={row.version}
          onChange={(e) => updateMetaRow(row.uid, 'version', e.target.value)}
        />
      ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      render: (_, row) => (
        <Input
          size="small"
          value={row.title}
          onChange={(e) => updateMetaRow(row.uid, 'title', e.target.value)}
        />
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <ProTable<DrawingRow>
        actionRef={actionRef}
        formRef={formRef}
        rowKey="id"
        headerTitle="图纸列表"
        columns={columns}
        rowSelection={{
          selectedRowKeys: selectedRows.map((r) => r.id),
          onChange: (_, rows) => setSelectedRows(rows),
        }}
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
            key="batch-review"
            icon={<RobotOutlined />}
            disabled={!selectedRows.length}
            onClick={handleBatchReview}
          >
            批量 AI 审图{selectedRows.length ? `（${selectedRows.length}）` : ''}
          </Button>,
          <Button
            key="full-set-review"
            icon={<AppstoreOutlined />}
            onClick={handleFullSetReview}
          >
            整套审图
          </Button>,
          <Button
            key="project-model"
            icon={<BuildOutlined />}
            onClick={handleOpenProjectModel}
          >
            工程模型
          </Button>,
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
        title="上传图纸（支持多文件批量）"
        open={uploadOpen}
        onCancel={closeUploadModal}
        onOk={handleUpload}
        confirmLoading={uploading}
        width={860}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="project_id" label="所属项目" rules={[{ required: true }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择项目"
              options={projectSelectOptions}
            />
          </Form.Item>
          {fileList.length <= 1 && (
            <Form.Item name="estimated_impact" label="预估影响金额（元）">
              <InputNumber style={{ width: '100%' }} min={0} step={10000} />
            </Form.Item>
          )}
          <Form.Item label="图纸文件" required>
            <Upload
              accept=".pdf,.dwg,.dxf,.ifc"
              multiple
              fileList={fileList}
              beforeUpload={() => false}
              onChange={({ fileList: fl }) => syncMetaRows(fl)}
            >
              <Button icon={<UploadOutlined />}>
                选择文件（PDF / DWG / DXF / IFC，单文件 ≤200MB，可多选）
              </Button>
            </Upload>
          </Form.Item>
          {metaRows.length > 0 && (
            <Space direction="vertical" style={{ width: '100%' }} size={4}>
              <Table<UploadMetaRow>
                size="small"
                rowKey="uid"
                columns={metaColumns}
                dataSource={metaRows}
                pagination={false}
                scroll={{ y: 280 }}
              />
            </Space>
          )}
        </Form>
      </Modal>
    </div>
  )
}
