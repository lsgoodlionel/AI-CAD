import { useEffect, useState } from 'react'
import {
  Card, Table, Tag, Space, Button, Tabs, Alert, Statistic, Row, Col,
  Typography, Tooltip,
} from 'antd'
import {
  FileExcelOutlined, FilePdfOutlined, ReloadOutlined,
  ExclamationCircleOutlined, WarningOutlined, InfoCircleOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons'
import { getAiReviewIssues, getAiReviewReportPdfUrl, getAiReviewReportExcelUrl } from '@/services/drawings'
import HelpTip from '@/components/HelpTip'
import ReviewFindings from './ReviewFindings'

const { Text } = Typography

interface AiReport {
  id: string
  status: string
  total_issues: number
  critical_issues: number
  completed_at?: string
}

interface Props {
  drawingId: string
  aiReport: AiReport | null
}

const SEVERITY_CONFIG = {
  critical: { label: '严重（强条）', color: 'red',    icon: <ExclamationCircleOutlined style={{ color: '#cf1322' }} /> },
  major:    { label: '重大',         color: 'orange', icon: <WarningOutlined style={{ color: '#d46b08' }} /> },
  minor:    { label: '一般',         color: 'blue',   icon: <InfoCircleOutlined style={{ color: '#0958d9' }} /> },
  info:     { label: '建议',         color: 'green',  icon: <CheckCircleOutlined style={{ color: '#389e0d' }} /> },
}

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  open:         { label: '待处理', color: 'default' },
  acknowledged: { label: '已知晓', color: 'blue' },
  closed:       { label: '已关闭', color: 'green' },
  waived:       { label: '已豁免', color: 'orange' },
}

const ENGINE_LABEL: Record<string, string> = {
  rule:   '规则引擎',
  rules:  '规则引擎',
  kg:     '知识图谱',
  rag:    'RAG检索',
  ocr:    '视觉OCR',
  review: '会审审查',
}

const COLUMNS = [
  {
    title: '严重程度',
    dataIndex: 'severity',
    width: 110,
    render: (v: string) => {
      const cfg = SEVERITY_CONFIG[v as keyof typeof SEVERITY_CONFIG]
      return cfg ? <Tag color={cfg.color}>{cfg.label}</Tag> : <Tag>{v}</Tag>
    },
  },
  {
    title: '引擎',
    dataIndex: 'engine',
    width: 90,
    render: (v: string) => ENGINE_LABEL[v] ?? v,
  },
  {
    title: '分类',
    dataIndex: 'category',
    width: 120,
    ellipsis: true,
  },
  {
    title: '问题描述',
    dataIndex: 'description',
    ellipsis: { showTitle: false },
    render: (v: string) => <Tooltip title={v}><Text style={{ maxWidth: 300 }} ellipsis>{v}</Text></Tooltip>,
  },
  {
    title: '规范条文',
    dataIndex: 'regulation_ref',
    width: 140,
    ellipsis: { showTitle: false },
    render: (v: string) => v ? <Tooltip title={v}><Text style={{ color: '#722ed1' }} ellipsis>{v}</Text></Tooltip> : '—',
  },
  {
    title: '整改建议',
    dataIndex: 'suggestion',
    ellipsis: { showTitle: false },
    render: (v: string) => v ? <Tooltip title={v}><Text ellipsis>{v}</Text></Tooltip> : '—',
  },
  {
    title: '状态',
    dataIndex: 'status',
    width: 80,
    render: (v: string) => {
      const cfg = STATUS_CONFIG[v] ?? { label: v, color: 'default' }
      return <Tag color={cfg.color}>{cfg.label}</Tag>
    },
  },
]

export default function AIReviewPanel({ drawingId, aiReport }: Props) {
  const [activeTab, setActiveTab] = useState('all')
  const [issues, setIssues] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 20

  const severityFilter = activeTab === 'all' ? undefined : activeTab

  const fetchIssues = async (pageNum = page) => {
    if (!aiReport || aiReport.status !== 'done') return
    if (activeTab === 'review') return  // 会审 Tab 由 ReviewFindings 自行取数
    setLoading(true)
    try {
      const res = await getAiReviewIssues(drawingId, {
        severity: severityFilter,
        limit: PAGE_SIZE,
        offset: (pageNum - 1) * PAGE_SIZE,
      })
      setIssues(res.items ?? [])
      setTotal(res.total ?? 0)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setPage(1)
    fetchIssues(1)
  }, [activeTab, drawingId])

  if (!aiReport) return null
  if (aiReport.status !== 'done') {
    return (
      <Card title="AI 审查报告" size="small" style={{ marginTop: 16 }}>
        <Alert type="info" showIcon message="AI 审查仍在进行中，请稍后刷新" />
      </Card>
    )
  }

  const hasCritical = aiReport.critical_issues > 0
  const counts: Record<string, number> = {}
  issues.forEach(i => { counts[i.severity] = (counts[i.severity] ?? 0) + 1 })

  const tabItems = [
    { key: 'all', label: `全部（${aiReport.total_issues}）` },
    ...(['critical', 'major', 'minor', 'info'] as const).map(sev => {
      const cfg = SEVERITY_CONFIG[sev]
      return { key: sev, label: <Tag color={cfg.color}>{cfg.label}</Tag> }
    }),
    { key: 'review', label: <Tag color="cyan">会审审查</Tag> },
  ]

  const isReviewTab = activeTab === 'review'

  const handleDownloadPdf = () => {
    window.open(getAiReviewReportPdfUrl(drawingId), '_blank')
  }

  const handleDownloadExcel = () => {
    window.open(getAiReviewReportExcelUrl(drawingId), '_blank')
  }

  return (
    <Card
      data-testid="ai-review-panel"
      title={
        <>
          AI 审查报告
          <HelpTip
            content="规则引擎、知识图谱、RAG 检索、视觉 OCR 四引擎与会审审查自动发现的问题清单，按严重度排序；严重（强条）问题必须全部关闭才能通过一审。"
            anchor=""
          />
        </>
      }
      size="small"
      style={{ marginTop: 16 }}
      extra={
        <Space>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => fetchIssues()}
            loading={loading}
          >
            刷新
          </Button>
          <Button
            size="small"
            icon={<FilePdfOutlined />}
            onClick={handleDownloadPdf}
            type="primary"
            ghost
          >
            批注版 PDF
          </Button>
          <Button
            size="small"
            icon={<FileExcelOutlined />}
            onClick={handleDownloadExcel}
            style={{ color: '#389e0d', borderColor: '#389e0d' }}
          >
            Excel 清单
          </Button>
        </Space>
      }
    >
      {hasCritical && (
        <Alert
          type="error"
          showIcon
          message={`发现 ${aiReport.critical_issues} 个强制性条文违规，一审通过前必须全部关闭`}
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={24} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Statistic title="问题总数" value={aiReport.total_issues} />
        </Col>
        {(['critical', 'major', 'minor', 'info'] as const).map(sev => (
          <Col span={4} key={sev}>
            <Statistic
              title={<span style={{ color: sev === 'critical' ? '#cf1322' : sev === 'major' ? '#d46b08' : sev === 'minor' ? '#0958d9' : '#389e0d' }}>{SEVERITY_CONFIG[sev].label}</span>}
              value={issues.filter(i => i.severity === sev).length || 0}
            />
          </Col>
        ))}
        <Col span={4}>
          <Statistic
            title="完成时间"
            value={aiReport.completed_at ? new Date(aiReport.completed_at).toLocaleDateString('zh-CN') : '—'}
          />
        </Col>
      </Row>

      <Tabs
        activeKey={activeTab}
        onChange={key => { setActiveTab(key); setPage(1) }}
        items={tabItems}
        size="small"
      />

      {isReviewTab ? (
        <ReviewFindings drawingId={drawingId} reportStatus={aiReport.status} />
      ) : (
      <Table
        size="small"
        loading={loading}
        dataSource={issues}
        rowKey="id"
        columns={COLUMNS}
        scroll={{ x: 900 }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total,
          showSizeChanger: false,
          showTotal: t => `共 ${t} 条`,
          onChange: p => { setPage(p); fetchIssues(p) },
        }}
        rowClassName={row => row.severity === 'critical' ? 'row-critical' : ''}
      />
      )}

      <style>{`.row-critical td { background: #fff1f0 !important; }`}</style>
    </Card>
  )
}
