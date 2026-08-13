import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { ProTable } from '@ant-design/pro-components'
import type { ActionType, ProColumns, ProFormInstance } from '@ant-design/pro-components'
import { Alert, Button, Modal, Space, Tag, message, Badge } from 'antd'
import type { PresetStatusColorType } from 'antd/es/_util/colors'
import {
  PlusOutlined, EyeOutlined, RobotOutlined, AppstoreOutlined, BuildOutlined,
} from '@ant-design/icons'
import { listDrawings, createReviewBatch } from '@/services/drawings'
import type { CreateReviewBatchResult } from '@/services/drawings'
import { listProjects } from '@/services/projects'
import {
  LOCATION_REASON_LABELS, LocationStatus, getLocationStatus,
} from '@/services/projectInfo'
import DrawingPreviewModal from '@/components/DrawingPreviewModal'
import UploadWizard, { DISCIPLINE_OPTIONS, DISCIPLINE_LABEL, extractErrorMessage } from './UploadWizard'
import DisciplineFilter from './DisciplineFilter'
import DirectoryDrawer from './DirectoryDrawer'

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
  /** 图框「专业」栏实读值(给排水/基坑围护…);null = 未读到,回落 discipline */
  discipline_label?: string | null
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
  const [preview, setPreview] =
    useState<{ id: string; title: string; projectId: string } | null>(null)
  // 专业快捷筛选(常驻一行,不必展开搜索表单)
  const [discipline, setDiscipline] = useState('')
  const [projectFilterId, setProjectFilterId] = useState<string | undefined>()
  // 未分层图的分类 —— 选定项目后才有意义（定位状态是按项目算的）
  const [locationStatus, setLocationStatus] = useState<LocationStatus | null>(null)

  useEffect(() => {
    listProjects({ limit: 200 }).then((res: { items?: ProjectOption[] }) =>
      setProjects(res.items ?? [])
    )
  }, [])

  useEffect(() => {
    if (!projectFilterId) { setLocationStatus(null); return }
    // 定位状态是**辅助信息**，拉不到不该影响图纸列表本身
    getLocationStatus(projectFilterId)
      .then((res) => setLocationStatus(res.data ?? null))
      .catch(() => setLocationStatus(null))
  }, [projectFilterId])

  /** drawing_id → 未分层项。**未选项目时为空**，此时该列不作判断 */
  const locationIndex = new Map(
    (locationStatus?.items ?? []).map((item) => [item.drawing_id, item]),
  )

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
      width: 100,
      // 图框实读专业优先(给排水/基坑围护…),读不到才回落粗粒度枚举
      render: (_, row) =>
        row.discipline_label ?? DISCIPLINE_LABEL[row.discipline] ?? row.discipline,
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
      // 只在选定项目后有值 —— 定位状态是按项目算的
      title: '定位',
      dataIndex: 'location_status',
      search: false,
      width: 110,
      render: (_, row) => {
        // **没选项目就是没数据，不是「都已定位」** —— 空 Map 会让每一行
        // 都显示成绿色的「已定位」，那是凭空捏造的结论。
        if (!locationStatus) return '—'
        const item = locationIndex.get(row.id)
        if (!item) return <Tag color="green">已定位</Tag>
        return (
          <Tag color={item.needs_floor_input ? 'orange' : 'default'}
               title={item.action}>
            {LOCATION_REASON_LABELS[item.reason] ?? item.reason}
            {item.hint ? `·${item.hint}` : ''}
          </Tag>
        )
      },
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
              setPreview({ id: row.id, title: `${row.drawing_no} ${row.title}`,
                projectId: row.project_id })
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

  // 专业切换即刷新列表(ProTable 的 request 闭包读最新 discipline)
  useEffect(() => { actionRef.current?.reload() }, [discipline])

  return (
    <div style={{ padding: 24 }}>
      <DisciplineFilter
        value={discipline}
        onChange={setDiscipline}
        projectId={projectFilterId}
      />
      {locationStatus && locationStatus.total > 0 && (
        <Alert
          type={locationStatus.actionable > 0 ? 'warning' : 'info'}
          showIcon style={{ marginBottom: 12 }}
          message={
            <>
              {locationStatus.total} 张图定位不到楼层，其中{' '}
              <b>{locationStatus.actionable} 张需要你补楼层</b>
            </>
          }
          description={
            <>
              <Space size={4} wrap style={{ marginBottom: 6 }}>
                {Object.entries(locationStatus.by_reason).map(([reason, n]) => (
                  <Tag key={reason}
                       color={reason === 'no_floor_by_nature' ? 'default' : 'orange'}>
                    {LOCATION_REASON_LABELS[reason] ?? reason} {n}
                  </Tag>
                ))}
              </Space>
              <div>
                {/* 「本就没有」与「该有却没有」必须分开报：building_unit_fallback
                    那轮原报 1866 张，拆开后真正要处理的只有 907 张（虚高 2.1 倍）*/}
                说明、目录、系统图<b>本就没有楼层</b>，不必处理；
                跨楼层表达的图硬指定楼层反而是错的；
                <b>非标准楼层名</b>只需告知系统它对应哪一层，不必翻图。
              </div>
            </>
          }
        />
      )}
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
          // 选了项目则专业计数随之收敛(只在变化时置状态,避免重复渲染)
          const pid = (rest as { project_id?: string }).project_id
          setProjectFilterId((prev) => (prev === pid ? prev : pid))
          const res = await listDrawings({
            ...rest,
            discipline_label: discipline || undefined,
            limit: pageSize,
            offset: ((current ?? 1) - 1) * (pageSize ?? 20),
          })
          return { data: res.items, total: res.total, success: true }
        }}
        pagination={{ pageSize: 20 }}
        toolBarRender={() => [
          <DirectoryDrawer key="directory" projectId={projectFilterId} />,
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
        projectId={preview?.projectId}
        onClose={() => setPreview(null)}
      />
    </div>
  )
}
