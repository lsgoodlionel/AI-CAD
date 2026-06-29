import { useEffect, useState } from 'react'
import { useParams, useNavigate, useModel } from '@umijs/max'
import {
  Card, Descriptions, Button, Spin, Space, Alert, Divider, Tag, Badge,
  Progress, Timeline, Typography, message,
} from 'antd'
import { ArrowLeftOutlined, DownloadOutlined, EyeOutlined, EyeInvisibleOutlined } from '@ant-design/icons'
import {
  getDrawing,
  getDownloadUrl,
  retryAiReview,
  startTechnicalReview,
} from '@/services/drawings'
import StatusTimeline from './StatusTimeline'
import AIReviewPanel from './AIReviewPanel'
import TechnicalReviewPanel from './TechnicalReviewPanel'
import EconomicReviewPanel from './EconomicReviewPanel'
import SettlementReviewPanel from './SettlementReviewPanel'
import EconomicCalcPanel from './EconomicCalcPanel'
import PdfViewer from './PdfViewer'

const { Text } = Typography

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  ai_reviewing: 'AI 审图中',
  ai_done: 'AI 审图完成',
  technical_review: '一审（技术规范化）',
  economic_review: '二审（经济最优化）',
  settlement_review: '三审（结算合规化）',
  published: '已发布',
  rejected: '已驳回',
}

const STATUS_COLOR: Record<string, string> = {
  draft: 'default',
  ai_reviewing: 'processing',
  ai_done: 'warning',
  technical_review: 'blue',
  economic_review: 'purple',
  settlement_review: 'orange',
  published: 'success',
  rejected: 'error',
}

const formatDuration = (seconds?: number) => {
  const total = Math.max(0, Math.round(seconds ?? 0))
  const mins = Math.floor(total / 60)
  const secs = total % 60
  if (mins <= 0) return `${secs}秒`
  return `${mins}分${secs.toString().padStart(2, '0')}秒`
}

function AIReviewProgressCard({ progress }: { progress: any }) {
  if (!progress) return null
  const timelineItems = [
    ...(progress.completed_parts ?? []).map((item: any) => ({
      color: 'green',
      children: (
        <Space direction="vertical" size={0}>
          <Text strong>{item.name}</Text>
          <Text type="secondary">{item.description}</Text>
        </Space>
      ),
    })),
    ...(progress.active_parts ?? []).map((item: any) => ({
      color: 'blue',
      children: (
        <Space direction="vertical" size={0}>
          <Text strong>{item.name}</Text>
          <Text type="secondary">{item.description}</Text>
        </Space>
      ),
    })),
    ...(progress.pending_parts ?? []).map((item: any) => ({
      color: 'gray',
      children: (
        <Space direction="vertical" size={0}>
          <Text type="secondary">{item.name}</Text>
          <Text type="secondary">{item.description}</Text>
        </Space>
      ),
    })),
  ]

  return (
    <Card title="AI 审图过程" style={{ marginBottom: 16 }}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Space align="center" wrap>
          <Progress
            type="circle"
            percent={progress.percent ?? 0}
            size={88}
            status={progress.status === 'failed' ? 'exception' : progress.status === 'done' ? 'success' : 'active'}
          />
          <Descriptions size="small" column={2}>
            <Descriptions.Item label="当前阶段">
              <Badge
                status={progress.status === 'failed' ? 'error' : progress.status === 'done' ? 'success' : 'processing'}
                text={progress.stage_name}
              />
            </Descriptions.Item>
            <Descriptions.Item label="阶段说明">
              {progress.stage_description}
            </Descriptions.Item>
            <Descriptions.Item label="已耗时">
              {formatDuration(progress.elapsed_seconds)}
            </Descriptions.Item>
            <Descriptions.Item label="预计剩余">
              {progress.status === 'done' ? '已完成' : formatDuration(progress.estimated_remaining_seconds)}
            </Descriptions.Item>
            <Descriptions.Item label="预计总时长">
              {formatDuration(progress.estimated_total_seconds)}
            </Descriptions.Item>
            <Descriptions.Item label="更新时间">
              {progress.updated_at ? new Date(progress.updated_at).toLocaleString('zh-CN') : '—'}
            </Descriptions.Item>
          </Descriptions>
        </Space>

        {(progress.warnings ?? []).map((warning: string) => (
          <Alert key={warning} type="warning" showIcon message={warning} />
        ))}

        <div>
          <Text strong>已完成 / 进行中 / 待完成</Text>
          <Timeline style={{ marginTop: 12 }} items={timelineItems} />
        </div>
      </Space>
    </Card>
  )
}

export default function DrawingDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { initialState } = useModel('@@initialState')
  const currentUser = (initialState as any)?.currentUser
  const userRole: string = currentUser?.role ?? ''

  const [drawing, setDrawing] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [pdfVisible, setPdfVisible] = useState(false)
  const [pdfLoading, setPdfLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const fetchDrawing = async (silent = false) => {
    if (!id) return
    if (!silent) setLoading(true)
    try {
      const data = await getDrawing(id)
      setDrawing(data)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => { fetchDrawing() }, [id])

  useEffect(() => {
    const aiStatus = drawing?.ai_report?.status
    if (drawing?.status !== 'ai_reviewing' && aiStatus !== 'processing' && aiStatus !== 'pending') return undefined
    const timer = window.setInterval(() => fetchDrawing(true), 5000)
    return () => window.clearInterval(timer)
  }, [drawing?.status, drawing?.ai_report?.status, id])

  const handleTogglePdf = async () => {
    if (pdfVisible) {
      setPdfVisible(false)
      return
    }
    if (!id) return
    if (!pdfUrl) {
      setPdfLoading(true)
      try {
        const res = await getDownloadUrl(id)
        setPdfUrl(res.url)
      } finally {
        setPdfLoading(false)
      }
    }
    setPdfVisible(true)
  }

  const handleDownload = async () => {
    if (!id) return
    const url = pdfUrl ?? (await getDownloadUrl(id)).url
    if (!pdfUrl) setPdfUrl(url)
    window.open(url, '_blank')
  }

  const handleRetryAiReview = async () => {
    if (!id) return
    setActionLoading('retry-ai')
    try {
      await retryAiReview(id)
      message.success('AI 审图任务已重新触发')
      await fetchDrawing(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '重新触发失败')
    } finally {
      setActionLoading(null)
    }
  }

  const handleStartTechnicalReview = async () => {
    if (!id) return
    setActionLoading('start-technical')
    try {
      await startTechnicalReview(id)
      message.success('已开启一审')
      await fetchDrawing(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '开启一审失败')
    } finally {
      setActionLoading(null)
    }
  }

  if (loading) return <Spin style={{ display: 'block', marginTop: 80 }} />
  if (!drawing) return <Alert type="error" message="图纸不存在" style={{ margin: 24 }} />

  const status: string = drawing.status

  return (
    <div data-testid="drawing-detail-page" style={{ padding: 24, maxWidth: 1100 }}>
      {/* 顶部导航 */}
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/drawings')}>
          返回列表
        </Button>
        <Button
          icon={pdfVisible ? <EyeInvisibleOutlined /> : <EyeOutlined />}
          loading={pdfLoading}
          onClick={handleTogglePdf}
        >
          {pdfVisible ? '收起图纸' : '查看图纸'}
        </Button>
        <Button icon={<DownloadOutlined />} onClick={handleDownload}>
          下载图纸
        </Button>
      </Space>

      {/* 基本信息 */}
      <Card style={{ marginBottom: 16 }}>
        <Descriptions
          title={
            <Space>
              <span>{drawing.drawing_no}</span>
              <Badge
                status={STATUS_COLOR[status] as any}
                text={STATUS_LABEL[status] ?? status}
              />
            </Space>
          }
          column={3}
          size="small"
        >
          <Descriptions.Item label="标题">{drawing.title || '—'}</Descriptions.Item>
          <Descriptions.Item label="专业">{drawing.discipline}</Descriptions.Item>
          <Descriptions.Item label="版次">{drawing.version}</Descriptions.Item>
          <Descriptions.Item label="所属项目">{drawing.project_name}</Descriptions.Item>
          <Descriptions.Item label="创建人">{drawing.creator_name}</Descriptions.Item>
          <Descriptions.Item label="预估金额">
            {drawing.estimated_impact
              ? `¥${(drawing.estimated_impact / 10000).toFixed(1)}万`
              : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="上传时间">
            {new Date(drawing.created_at).toLocaleString('zh-CN')}
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {new Date(drawing.updated_at).toLocaleString('zh-CN')}
          </Descriptions.Item>
          {drawing.finance_lock_status === 'pending_escalation' && (
            <Descriptions.Item label="财务">
              <Tag color="red">重大变更 ≥50万，待升级审批</Tag>
            </Descriptions.Item>
          )}
        </Descriptions>

        {/* AI 审图报告简报 */}
        {drawing.ai_report && (
          <>
            <Divider style={{ margin: '12px 0' }} />
            <Descriptions size="small" column={4} title="AI 审图报告">
              <Descriptions.Item label="状态">
                <Badge
                  status={drawing.ai_report.status === 'done' ? 'success' : 'processing'}
                  text={drawing.ai_report.status === 'done' ? '完成' : '处理中'}
                />
              </Descriptions.Item>
              <Descriptions.Item label="总问题数">
                {drawing.ai_report.total_issues ?? '—'}
              </Descriptions.Item>
              <Descriptions.Item label="严重问题">
                {drawing.ai_report.critical_issues > 0
                  ? <Tag color="red">{drawing.ai_report.critical_issues}</Tag>
                  : <Tag color="green">0</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="完成时间">
                {drawing.ai_report.completed_at
                  ? new Date(drawing.ai_report.completed_at).toLocaleString('zh-CN')
                  : '—'}
              </Descriptions.Item>
            </Descriptions>
          </>
        )}
      </Card>

      {drawing.ai_report?.progress && (
        <AIReviewProgressCard progress={drawing.ai_report.progress} />
      )}

      {/* 图纸内嵌预览 */}
      {pdfVisible && pdfUrl && (
        <Card style={{ marginBottom: 16 }} bodyStyle={{ padding: 12 }}>
          <PdfViewer url={pdfUrl} />
        </Card>
      )}

      {/* 状态时间轴 */}
      <Card style={{ marginBottom: 16 }}>
        <StatusTimeline status={status} />
      </Card>

      {/* AI 审查报告面板（AI审查完成后始终显示） */}
      {drawing.ai_report?.status === 'done' && (
        <AIReviewPanel drawingId={id!} aiReport={drawing.ai_report} />
      )}

      {status === 'ai_done' && (
        <Alert
          type="info"
          showIcon
          message="AI 审图已完成"
          description="可开启一审，进入技术规范化审批。"
          action={
            <Button
              type="primary"
              loading={actionLoading === 'start-technical'}
              onClick={handleStartTechnicalReview}
            >
              开启一审
            </Button>
          }
          style={{ marginTop: 16 }}
        />
      )}

      {/* 当前审批面板（按状态显示） */}
      {status === 'technical_review' && (
        <TechnicalReviewPanel
          drawingId={id!}
          userRole={userRole}
          aiReport={drawing.ai_report}
          onRefresh={fetchDrawing}
        />
      )}

      {status === 'economic_review' && (
        <EconomicReviewPanel
          drawingId={id!}
          userRole={userRole}
          onRefresh={fetchDrawing}
        />
      )}

      {status === 'settlement_review' && (
        <SettlementReviewPanel
          drawingId={id!}
          userRole={userRole}
          onRefresh={fetchDrawing}
        />
      )}

      {status === 'published' && (
        <Alert type="success" showIcon message="图纸已发布至班组，三审流程完成" />
      )}

      {/* 经济测算面板（二审及之后阶段始终可用） */}
      {['economic_review', 'settlement_review', 'published'].includes(status) && (
        <EconomicCalcPanel
          drawingId={id!}
          drawingNo={drawing.drawing_no}
        />
      )}

      {status === 'rejected' && (
        <Alert type="error" showIcon message="图纸已被驳回，请修改后重新上传" />
      )}

      {(status === 'draft' || status === 'ai_reviewing') && (
        <Alert
          type="info"
          showIcon
          message={
            status === 'draft' ? '图纸处于草稿状态'
            : 'AI 审图进行中，可在上方查看阶段进度、已完成内容和预计剩余时间'
          }
          action={
            status === 'ai_reviewing' ? (
              <Button
                loading={actionLoading === 'retry-ai'}
                onClick={handleRetryAiReview}
              >
                重新触发审图
              </Button>
            ) : undefined
          }
        />
      )}
    </div>
  )
}
