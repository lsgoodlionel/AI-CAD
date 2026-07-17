import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from '@umijs/max'
import {
  Badge, Button, Card, Col, Empty, Progress, Row, Space, Spin, Table, Tag, Typography, message,
} from 'antd'
import type { TableProps } from 'antd'
import { ArrowLeftOutlined, BuildOutlined, EyeOutlined } from '@ant-design/icons'
import DrawingPreviewModal from '@/components/DrawingPreviewModal'
import {
  getReviewBatch,
  coerceJson,
  REVIEW_BATCH_STATUS_META,
  REVIEW_BATCH_SCOPE_META,
  REVIEW_BATCH_TERMINAL_STATUSES,
} from '@/services/drawings'
import type {
  CrossDrawingFindings,
  ReviewBatchDetail,
  ReviewBatchDrawingItem,
} from '@/services/drawings'

const { Text } = Typography

const POLL_INTERVAL_MS = 5000

const DISCIPLINE_LABEL: Record<string, string> = {
  structure: '结构',
  architecture: '建筑',
  mep: '机电',
  curtain_wall: '幕墙',
  decoration: '精装',
  other: '其他',
}

const REPORT_STATUS_META: Record<string, { badge: 'default' | 'processing' | 'success' | 'error'; text: string }> = {
  pending: { badge: 'default', text: '等待中' },
  processing: { badge: 'processing', text: '审图中' },
  done: { badge: 'success', text: '已完成' },
  failed: { badge: 'error', text: '失败' },
}

const SEVERITY_META: Record<string, { color: string; label: string }> = {
  critical: { color: 'red', label: '严重（强条）' },
  major: { color: 'orange', label: '重大' },
  minor: { color: 'blue', label: '一般' },
  info: { color: 'green', label: '建议' },
}

/** 分布类字段（严重度/专业）统一渲染为「标签 × 数量」 */
function DistributionTags({ data, meta }: {
  data: Record<string, number>
  meta?: Record<string, { color: string; label: string }>
}) {
  const entries = Object.entries(data).filter(([, count]) => count > 0)
  if (!entries.length) return <Text type="secondary">暂无数据</Text>
  return (
    <Space wrap size={4}>
      {entries.map(([key, count]) => {
        const m = meta?.[key]
        return (
          <Tag key={key} color={m?.color ?? 'default'}>
            {m?.label ?? key} × {count}
          </Tag>
        )
      })}
    </Space>
  )
}

interface FindingCardProps {
  title: string
  isEmpty: boolean
  children: React.ReactNode
}

function FindingCard({ title, isEmpty, children }: FindingCardProps) {
  return (
    <Card size="small" title={title} style={{ height: '100%' }}>
      {isEmpty ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未发现问题" />
      ) : (
        children
      )}
    </Card>
  )
}

/** 跨图发现四类卡片 + 高频对象聚合 */
function CrossFindingsSection({ findings }: { findings: CrossDrawingFindings }) {
  const duplicates = findings.重复图号 ?? []
  const conflicts = findings.版本冲突 ?? []
  const missing = findings.接口缺图 ?? []
  const clusters = findings.问题聚类 ?? []
  const hotObjects = findings.高频对象聚合 ?? []

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={12}>
        <FindingCard title="重复图号" isEmpty={!duplicates.length}>
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {duplicates.map((d) => (
              <div key={d.drawing_no}>
                <Tag color="red">{d.drawing_no}</Tag>
                <Text type="secondary">重复 {d.drawing_ids.length} 张</Text>
              </div>
            ))}
          </Space>
        </FindingCard>
      </Col>
      <Col xs={24} md={12}>
        <FindingCard title="版本冲突（同图号多版本在审）" isEmpty={!conflicts.length}>
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {conflicts.map((c) => (
              <div key={c.drawing_no}>
                <Tag color="orange">{c.drawing_no}</Tag>
                {c.versions.map((v) => (
                  <Tag key={v}>{v}</Tag>
                ))}
              </div>
            ))}
          </Space>
        </FindingCard>
      </Col>
      <Col xs={24} md={12}>
        <FindingCard title="接口缺图" isEmpty={!missing.length}>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {missing.map((m) => (
              <div key={m.missing_discipline}>
                <div>
                  缺 <Tag color="volcano">{m.missing_discipline}</Tag> 专业图纸
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  被引用：
                  {m.referenced_by
                    .map((r) => `${r.drawing_no}（${r.interface}）`)
                    .join('、')}
                </Text>
              </div>
            ))}
          </Space>
        </FindingCard>
      </Col>
      <Col xs={24} md={12}>
        <FindingCard title="问题聚类（≥2 张图共有）" isEmpty={!clusters.length}>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {clusters.map((c) => (
              <div key={c.location_key}>
                <div>
                  <Tag color="geekblue">{c.location_key}</Tag>
                  <Text>共 {c.count} 处</Text>
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  涉及图纸：{c.drawings.join('、')}　专业：{c.disciplines.join('、')}
                </Text>
              </div>
            ))}
          </Space>
        </FindingCard>
      </Col>
      {hotObjects.length > 0 && (
        <Col span={24}>
          <Card size="small" title="高频对象聚合">
            <Space wrap size={4}>
              {hotObjects.map((o) => (
                <Tag key={o.name} color="purple">
                  {o.name} × {o.count}
                </Tag>
              ))}
            </Space>
          </Card>
        </Col>
      )}
    </Row>
  )
}

export default function ReviewBatchDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<ReviewBatchDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState<{ id: string; title: string } | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    if (!id) return
    let cancelled = false

    const load = async () => {
      try {
        const res = await getReviewBatch(id)
        if (cancelled) return
        setDetail(res)
        setLoading(false)
        // 非终态（pending/processing）每 5s 轮询
        if (!REVIEW_BATCH_TERMINAL_STATUSES.includes(res.batch.status)) {
          timerRef.current = setTimeout(load, POLL_INTERVAL_MS)
        }
      } catch {
        if (cancelled) return
        setLoading(false)
        message.error('加载套图审查详情失败')
      }
    }

    load()
    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [id])

  if (loading && !detail) {
    return (
      <div style={{ padding: 80, textAlign: 'center' }}>
        <Spin tip="加载中…" />
      </div>
    )
  }

  if (!detail) {
    return (
      <div style={{ padding: 80 }}>
        <Empty description="套图审查任务不存在或加载失败">
          <Button onClick={() => navigate('/drawings/review-batches')}>返回任务列表</Button>
        </Empty>
      </div>
    )
  }

  const { batch, items, progress } = detail
  const statusMeta = REVIEW_BATCH_STATUS_META[batch.status] ?? {
    badge: 'default' as const,
    text: batch.status,
  }
  const scopeMeta = REVIEW_BATCH_SCOPE_META[batch.scope] ?? { color: 'default', text: batch.scope }
  const finishedCount = progress.done + progress.failed
  const percent = progress.total ? Math.round((finishedCount / progress.total) * 100) : 0
  const successPercent = progress.total ? Math.round((progress.done / progress.total) * 100) : 0
  const isTerminal = REVIEW_BATCH_TERMINAL_STATUSES.includes(batch.status)
  const progressStatus = !isTerminal
    ? ('active' as const)
    : batch.status === 'done'
      ? ('success' as const)
      : ('exception' as const)

  const findings = coerceJson<CrossDrawingFindings>(batch.cross_findings, {})
  const severityDist = findings.严重度分布 ?? {}
  const disciplineDist = findings.专业分布 ?? {}

  const itemColumns: TableProps<ReviewBatchDrawingItem>['columns'] = [
    {
      title: '图号',
      dataIndex: 'drawing_no',
      width: 140,
      render: (_, row) => (
        <a onClick={() => navigate(`/drawings/${row.drawing_id}`)}>{row.drawing_no}</a>
      ),
    },
    { title: '标题', dataIndex: 'title', ellipsis: true },
    {
      title: '专业',
      dataIndex: 'discipline',
      width: 90,
      render: (_, row) => DISCIPLINE_LABEL[row.discipline] ?? row.discipline,
    },
    {
      title: '审图状态',
      dataIndex: 'report_status',
      width: 110,
      render: (_, row) => {
        const meta = REPORT_STATUS_META[row.report_status] ?? {
          badge: 'default' as const,
          text: row.report_status,
        }
        return <Badge status={meta.badge} text={meta.text} />
      },
    },
    {
      title: '问题数',
      dataIndex: 'total_issues',
      width: 90,
      align: 'right',
    },
    {
      title: '严重问题',
      dataIndex: 'critical_issues',
      width: 90,
      align: 'right',
      render: (_, row) =>
        row.critical_issues > 0 ? (
          <Text type="danger" strong>
            {row.critical_issues}
          </Text>
        ) : (
          row.critical_issues
        ),
    },
    {
      title: '操作',
      width: 140,
      render: (_, row) => (
        <>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() =>
              setPreview({ id: row.drawing_id, title: `${row.drawing_no} ${row.title}` })
            }
          >
            预览
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => navigate(`/drawings/${row.drawing_id}`)}
          >
            详情
          </Button>
        </>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space>
              <Button
                type="text"
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate('/drawings/review-batches')}
              />
              <span>套图审查任务</span>
              <Text copyable={{ text: batch.id }} type="secondary" style={{ fontWeight: 'normal' }}>
                {batch.id.slice(0, 8)}
              </Text>
              <Tag color={scopeMeta.color}>{scopeMeta.text}</Tag>
              <Badge status={statusMeta.badge} text={statusMeta.text} />
            </Space>
          }
        >
          <Progress
            percent={percent}
            success={{ percent: successPercent }}
            status={progressStatus}
          />
          <Space size={24} style={{ marginTop: 8 }}>
            <Text>共 {progress.total} 张</Text>
            <Text type="success">完成 {progress.done}</Text>
            <Text type="danger">失败 {progress.failed}</Text>
            <Text type="secondary">进行中 {progress.processing}</Text>
            {!isTerminal && <Text type="secondary">（每 5 秒自动刷新）</Text>}
          </Space>
        </Card>

        <Card size="small" title="图纸审查明细">
          <Table<ReviewBatchDrawingItem>
            size="small"
            rowKey="drawing_id"
            columns={itemColumns}
            dataSource={items}
            pagination={items.length > 20 ? { pageSize: 20 } : false}
          />
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} md={12}>
            <Card size="small" title="严重度分布" style={{ height: '100%' }}>
              <DistributionTags data={severityDist} meta={SEVERITY_META} />
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card size="small" title="专业分布" style={{ height: '100%' }}>
              <DistributionTags data={disciplineDist} />
            </Card>
          </Col>
        </Row>

        <Card
          size="small"
          title="跨图发现"
          styles={{ body: { padding: 16 } }}
          extra={
            (batch.status === 'done' || batch.status === 'partial_failed') && (
              <Button
                size="small"
                icon={<BuildOutlined />}
                onClick={() => navigate(`/model/${batch.project_id}`)}
              >
                在模型中查看跨图发现
              </Button>
            )
          }
        >
          {isTerminal ? (
            <CrossFindingsSection findings={findings} />
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="套图审查完成后生成跨图分析结果"
            />
          )}
        </Card>
      </Space>

      <DrawingPreviewModal
        drawingId={preview?.id ?? null}
        title={preview?.title}
        onClose={() => setPreview(null)}
      />
    </div>
  )
}
