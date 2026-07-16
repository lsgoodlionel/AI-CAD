import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { ProTable } from '@ant-design/pro-components'
import type { ActionType, ProColumns, ProFormInstance } from '@ant-design/pro-components'
import { Button, Modal, message, Badge } from 'antd'
import type { PresetStatusColorType } from 'antd/es/_util/colors'
import {
  PlusOutlined, EyeOutlined, RobotOutlined, AppstoreOutlined, BuildOutlined,
} from '@ant-design/icons'
import { listDrawings, createReviewBatch } from '@/services/drawings'
import type { CreateReviewBatchResult } from '@/services/drawings'
import { listProjects } from '@/services/projects'
import DrawingPreviewModal from '@/components/DrawingPreviewModal'
import UploadWizard, { DISCIPLINE_OPTIONS, DISCIPLINE_LABEL, extractErrorMessage } from './UploadWizard'

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

export default function DrawingList() {
  const actionRef = useRef<ActionType>()
  const formRef = useRef<ProFormInstance>()
  const navigate = useNavigate()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [selectedRows, setSelectedRows] = useState<DrawingRow[]>([])
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [preview, setPreview] = useState<{ id: string; title: string } | null>(null)

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
      width: 140,
      search: false,
      render: (_, row) => (
        <>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() =>
              setPreview({ id: row.id, title: `${row.drawing_no} ${row.title}` })
            }
          >
            预览
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => navigate(`/drawings/${row.id}`)}
          >
            详情
          </Button>
        </>
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

      <UploadWizard
        open={uploadOpen}
        projectSelectOptions={projectSelectOptions}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => actionRef.current?.reload()}
      />

      <DrawingPreviewModal
        drawingId={preview?.id ?? null}
        title={preview?.title}
        onClose={() => setPreview(null)}
      />
    </div>
  )
}
