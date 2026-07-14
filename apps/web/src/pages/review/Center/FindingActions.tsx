/**
 * 单条 Finding 的操作区：
 * 1) 闭环状态机：按当前 status 显示唯一可流转的下一态
 *    （pending→acknowledged→remediated→closed，单向不可回退）。
 *    后端对回退/非法流转返回 409，这里捕获后给出友好提示而非泛化报错。
 * 2) D-07「转创效提案」：仅当 finding.has_saving_potential 为 true 时展示，
 *    点击一键创建创效提案**草稿**并跳转到提案详情（/incentive/:id）；
 *    后端规则未命中创效潜力时返回 409（NO_SAVING_POTENTIAL），这里捕获后友好提示。
 *    与状态流转互不影响——即便已闭环（已整改）也允许补建创效线索。
 */
import { useState } from 'react'
import { useNavigate } from '@umijs/max'
import { Button, Input, message, Space, Tag } from 'antd'
import { CheckCircleOutlined, DollarOutlined } from '@ant-design/icons'
import type { Finding, FindingStatus } from '@/services/findings'
import {
  STATUS_META,
  findingToProposal,
  nextStatus,
  parseFindingId,
  updateFindingStatus,
} from '@/services/findings'

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
  const navigate = useNavigate()
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [convertingToProposal, setConvertingToProposal] = useState(false)

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

  const handleToProposal = async (): Promise<void> => {
    setConvertingToProposal(true)
    try {
      const { sourceKey } = parseFindingId(finding.id)
      const res = await findingToProposal(projectId, finding.source, sourceKey)
      if (res.success) {
        message.success('已生成创效提案草稿，正在跳转…')
        navigate(`/incentive/${res.data.proposal_id}`)
      } else {
        message.error(res.error || '创建创效提案草稿失败')
      }
    } catch (err: unknown) {
      if (isConflictError(err)) {
        message.warning('该问题暂无创效潜力（规则/LLM 均未命中），无法转创效提案')
      } else {
        message.error('创建创效提案草稿失败，请稍后重试')
      }
    } finally {
      setConvertingToProposal(false)
    }
  }

  const toProposalButton = finding.has_saving_potential ? (
    <Button icon={<DollarOutlined />} loading={convertingToProposal} onClick={handleToProposal}>
      转创效提案
    </Button>
  ) : null

  if (!target) {
    return (
      <Space>
        <Tag icon={<CheckCircleOutlined />} color="success">
          已闭环，流程结束
        </Tag>
        {toProposalButton}
      </Space>
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
      {toProposalButton}
    </Space>
  )
}
