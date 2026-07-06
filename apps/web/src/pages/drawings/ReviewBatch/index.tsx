import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { ProTable } from '@ant-design/pro-components'
import type { ActionType, ProColumns } from '@ant-design/pro-components'
import { Badge, Tag, Typography } from 'antd'
import {
  listReviewBatches,
  coerceJson,
  REVIEW_BATCH_STATUS_META,
  REVIEW_BATCH_SCOPE_META,
} from '@/services/drawings'
import type { ReviewBatch, ReviewBatchSummary } from '@/services/drawings'
import { listProjects } from '@/services/projects'

const { Text } = Typography

interface ProjectOption {
  id: string
  name: string
  code?: string
}

/** 进度展示：summary 已生成则 done/total，否则以 drawing_ids 数量兜底 */
function progressText(row: ReviewBatch): string {
  const summary = coerceJson<ReviewBatchSummary | null>(row.summary, null)
  if (summary) return `${summary.done} / ${summary.total}`
  const ids = coerceJson<string[]>(row.drawing_ids, [])
  return `0 / ${ids.length}`
}

export default function ReviewBatchList() {
  const actionRef = useRef<ActionType>()
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectOption[]>([])

  useEffect(() => {
    listProjects({ limit: 200 }).then((res: { items?: ProjectOption[] }) =>
      setProjects(res.items ?? [])
    )
  }, [])

  const columns: ProColumns<ReviewBatch>[] = [
    {
      title: '所属项目',
      dataIndex: 'project_id',
      hideInTable: true,
      valueType: 'select',
      fieldProps: {
        showSearch: true,
        optionFilterProp: 'label',
        placeholder: '按项目筛选',
        options: projects.map((p) => ({
          label: `${p.name}${p.code ? ` (${p.code})` : ''}`,
          value: p.id,
        })),
      },
    },
    {
      title: '批次号',
      dataIndex: 'id',
      width: 120,
      search: false,
      render: (_, row) => <Text copyable={{ text: row.id }}>{row.id.slice(0, 8)}</Text>,
    },
    {
      title: '范围',
      dataIndex: 'scope',
      width: 90,
      search: false,
      render: (_, row) => {
        const meta = REVIEW_BATCH_SCOPE_META[row.scope] ?? { color: 'default', text: row.scope }
        return <Tag color={meta.color}>{meta.text}</Tag>
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      search: false,
      render: (_, row) => {
        const meta = REVIEW_BATCH_STATUS_META[row.status] ?? { badge: 'default' as const, text: row.status }
        return <Badge status={meta.badge} text={meta.text} />
      },
    },
    {
      title: '进度（完成/总数）',
      width: 140,
      search: false,
      render: (_, row) => progressText(row),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      search: false,
      width: 160,
      render: (_, row) => new Date(row.created_at).toLocaleString('zh-CN'),
    },
    {
      title: '完成时间',
      dataIndex: 'completed_at',
      search: false,
      width: 160,
      render: (_, row) =>
        row.completed_at ? new Date(row.completed_at).toLocaleString('zh-CN') : '—',
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <ProTable<ReviewBatch>
        actionRef={actionRef}
        rowKey="id"
        headerTitle="套图审查任务"
        columns={columns}
        request={async (params) => {
          const { current, pageSize, project_id } = params
          const res = await listReviewBatches({
            project_id,
            limit: pageSize,
            offset: ((current ?? 1) - 1) * (pageSize ?? 20),
          })
          return { data: res.items, total: res.total, success: true }
        }}
        pagination={{ pageSize: 20 }}
        onRow={(row) => ({
          style: { cursor: 'pointer' },
          onClick: () => navigate(`/drawings/review-batches/${row.id}`),
        })}
      />
    </div>
  )
}
