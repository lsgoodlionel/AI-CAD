/**
 * Finding 列表表格：主列展示来源/严重度/标题/状态，展开行显示详情
 * （描述全文/定位 JSON/备注）+ FindingActions 闭环操作按钮。
 */
import { useNavigate } from '@umijs/max'
import { Table, Tag, Typography } from 'antd'
import type { Finding } from '@/services/findings'
import { SEVERITY_META, SOURCE_META, STATUS_META } from '@/services/findings'
import FindingActions from './FindingActions'

const { Text, Paragraph } = Typography

interface FindingListProps {
  projectId: string
  data: Finding[]
  loading: boolean
  onUpdated: (updated: Finding) => void
  showSourceColumn: boolean
}

function formatCreatedAt(value?: string | null): string {
  if (!value) return '—'
  const t = new Date(value)
  return Number.isNaN(t.getTime()) ? '—' : t.toLocaleString('zh-CN')
}

export default function FindingList({
  projectId,
  data,
  loading,
  onUpdated,
  showSourceColumn,
}: FindingListProps): JSX.Element {
  const navigate = useNavigate()

  const columns = [
    ...(showSourceColumn
      ? [
          {
            title: '来源',
            dataIndex: 'source',
            width: 80,
            render: (source: Finding['source']) => (
              <Tag color={SOURCE_META[source]?.color}>{SOURCE_META[source]?.label ?? source}</Tag>
            ),
          },
        ]
      : []),
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 90,
      render: (severity: Finding['severity']) => (
        <Tag color={SEVERITY_META[severity]?.color}>{SEVERITY_META[severity]?.label ?? severity}</Tag>
      ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      render: (title: string, row: Finding) => (
        <div>
          <Text strong>{title}</Text>
          <br />
          <Text type="secondary" ellipsis style={{ maxWidth: 480, display: 'inline-block' }}>
            {row.description}
          </Text>
        </div>
      ),
    },
    {
      title: '图纸',
      dataIndex: 'drawing_id',
      width: 110,
      render: (drawingId: string | null) =>
        drawingId ? (
          <a onClick={() => navigate(`/drawings/${drawingId}`)}>{drawingId.slice(0, 8)}</a>
        ) : (
          '—'
        ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: Finding['status']) => (
        <Tag color={STATUS_META[status]?.color}>{STATUS_META[status]?.label ?? status}</Tag>
      ),
    },
    {
      title: '发现时间',
      dataIndex: 'created_at',
      width: 160,
      render: formatCreatedAt,
    },
  ]

  return (
    <Table<Finding>
      rowKey="id"
      loading={loading}
      columns={columns}
      dataSource={data}
      pagination={{ pageSize: 20, showTotal: (total) => `共 ${total} 条` }}
      expandable={{
        expandedRowRender: (row) => (
          <div style={{ padding: '8px 0' }}>
            <Paragraph>
              <Text type="secondary">问题描述：</Text>
              {row.description || '（无详细描述）'}
            </Paragraph>
            {row.location ? (
              <Paragraph>
                <Text type="secondary">定位信息：</Text>
                <Text code>{JSON.stringify(row.location)}</Text>
              </Paragraph>
            ) : null}
            {row.note ? (
              <Paragraph>
                <Text type="secondary">处理备注：</Text>
                {row.note}
              </Paragraph>
            ) : null}
            <FindingActions projectId={projectId} finding={row} onUpdated={onUpdated} />
          </div>
        ),
      }}
    />
  )
}
