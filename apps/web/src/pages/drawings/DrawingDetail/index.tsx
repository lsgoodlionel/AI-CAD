import { useEffect, useState } from 'react'
import { useParams, useNavigate, useModel } from '@umijs/max'
import {
  Card, Descriptions, Button, Spin, Space, Alert, Divider, Tag, Badge,
} from 'antd'
import { ArrowLeftOutlined, DownloadOutlined } from '@ant-design/icons'
import { getDrawing, getDownloadUrl } from '@/services/drawings'
import StatusTimeline from './StatusTimeline'
import AIReviewPanel from './AIReviewPanel'
import TechnicalReviewPanel from './TechnicalReviewPanel'
import EconomicReviewPanel from './EconomicReviewPanel'
import SettlementReviewPanel from './SettlementReviewPanel'
import EconomicCalcPanel from './EconomicCalcPanel'

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

export default function DrawingDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { initialState } = useModel('@@initialState')
  const currentUser = (initialState as any)?.currentUser
  const userRole: string = currentUser?.role ?? ''

  const [drawing, setDrawing] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)

  const fetchDrawing = async () => {
    if (!id) return
    setLoading(true)
    try {
      const data = await getDrawing(id)
      setDrawing(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchDrawing() }, [id])

  const handleDownload = async () => {
    if (!id) return
    const res = await getDownloadUrl(id)
    setPdfUrl(res.url)
    window.open(res.url, '_blank')
  }

  if (loading) return <Spin style={{ display: 'block', marginTop: 80 }} />
  if (!drawing) return <Alert type="error" message="图纸不存在" style={{ margin: 24 }} />

  const status: string = drawing.status

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      {/* 顶部导航 */}
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/drawings')}>
          返回列表
        </Button>
        <Button icon={<DownloadOutlined />} onClick={handleDownload}>
          下载原图纸
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
          message="AI 审图已完成，等待开启一审（项目总工操作）"
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
            : 'AI 审图进行中，请等待完成后开始一审'
          }
        />
      )}
    </div>
  )
}
