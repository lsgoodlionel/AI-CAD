/**
 * 单条 Finding 的闭环状态机操作：按当前 status 显示唯一可流转的下一态
 * （pending→acknowledged→remediated→closed，单向不可回退）。
 * 后端对回退/非法流转返回 409，这里捕获后给出友好提示而非泛化报错。
 */
import { useState } from 'react'
import { Button, Input, message, Space, Tag } from 'antd'
import { CheckCircleOutlined } from '@ant-design/icons'
import type { Finding, FindingStatus } from '@/services/findings'
import { STATUS_META, nextStatus, parseFindingId, updateFindingStatus } from '@/services/findings'

const NEXT_ACTION_LABEL: Record<FindingStatus, string> = {
  pending: '确认',
  acknowledged: '标记已整改',
  remediated: '闭环',
  closed: '',
}

interface FindingActionsProps {
  projectId: string
  finding: Finding
  onUpdated: (updated: Finding) => void
}

function isConflictError(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 409
}

export default function FindingActions({
  projectId,
  finding,
  onUpdated,
}: FindingActionsProps): JSX.Element {
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const target = nextStatus(finding.status)

  const handleAdvance = async (): Promise<void> => {
    if (!target) return
    setSubmitting(true)
    try {
      const { sourceKey } = parseFindingId(finding.id)
      const res = await updateFindingStatus(projectId, finding.source, sourceKey, {
        status: target,
        note: note || undefined,
      })
      if (res.success) {
        onUpdated({
          ...finding,
          status: res.data.status,
          note: res.data.note,
          status_updated_at: res.data.status_updated_at,
        })
        message.success(`已流转到「${STATUS_META[target].label}」`)
        setNote('')
      } else {
        message.error(res.error || '状态更新失败')
      }
    } catch (err: unknown) {
      if (isConflictError(err)) {
        message.warning('该问题状态已发生变化（不可回退），请刷新后重试')
      } else {
        message.error('状态更新失败，请稍后重试')
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (!target) {
    return (
      <Tag icon={<CheckCircleOutlined />} color="success">
        已闭环，流程结束
      </Tag>
    )
  }

  return (
    <Space>
      <Input
        placeholder="备注（可选）"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        style={{ width: 240 }}
        maxLength={2000}
      />
      <Button type="primary" loading={submitting} onClick={handleAdvance}>
        {NEXT_ACTION_LABEL[finding.status]} → {STATUS_META[target].label}
      </Button>
    </Space>
  )
}
