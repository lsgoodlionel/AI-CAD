import { Steps, Tag } from 'antd'
import {
  RobotOutlined, AuditOutlined, DollarOutlined, FileDoneOutlined, CheckCircleOutlined,
} from '@ant-design/icons'

const STEPS = [
  { key: 'ai_reviewing', label: 'AI 审图', icon: <RobotOutlined /> },
  { key: 'technical_review', label: '一审', icon: <AuditOutlined /> },
  { key: 'economic_review', label: '二审', icon: <DollarOutlined /> },
  { key: 'settlement_review', label: '三审', icon: <FileDoneOutlined /> },
  { key: 'published', label: '已发布', icon: <CheckCircleOutlined /> },
]

const STATE_TO_STEP: Record<string, number> = {
  draft:             -1,
  ai_reviewing:       0,
  ai_done:            0,
  technical_review:   1,
  economic_review:    2,
  settlement_review:  3,
  published:          4,
  rejected:          -2,
}

interface Props {
  status: string
}

export default function StatusTimeline({ status }: Props) {
  if (status === 'rejected') {
    return (
      <div style={{ padding: '12px 0' }}>
        <Tag color="error" style={{ fontSize: 14, padding: '4px 12px' }}>已驳回</Tag>
      </div>
    )
  }

  if (status === 'draft') {
    return (
      <div style={{ padding: '12px 0' }}>
        <Tag color="default" style={{ fontSize: 14, padding: '4px 12px' }}>草稿 — 等待 AI 审图</Tag>
      </div>
    )
  }

  const current = STATE_TO_STEP[status] ?? 0

  return (
    <Steps
      current={current}
      status={status === 'published' ? 'finish' : 'process'}
      items={STEPS.map((s, i) => ({
        title: s.label,
        icon: s.icon,
        status:
          i < current ? 'finish'
          : i === current ? (status === 'published' ? 'finish' : 'process')
          : 'wait',
      }))}
      style={{ marginTop: 8 }}
    />
  )
}
