import { useState } from 'react'
import { Card, Form, Checkbox, Input, Button, Space, Alert, message, Divider } from 'antd'
import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import { submitTechnicalReview } from '@/services/drawings'

const ALLOWED_ROLES = ['project_chief_engineer', 'group_admin', 'group_chief_engineer']

interface Props {
  drawingId: string
  userRole: string
  aiReport?: { status: string; total_issues: number; critical_issues: number } | null
  onRefresh: () => void
}

export default function TechnicalReviewPanel({ drawingId, userRole, aiReport, onRefresh }: Props) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  const canReview = ALLOWED_ROLES.includes(userRole)

  const submit = async (result: 'approved' | 'rejected') => {
    const values = await form.validateFields()
    setLoading(true)
    try {
      await submitTechnicalReview(drawingId, { result, ...values })
      message.success(result === 'approved' ? '一审通过，已进入二审' : '已驳回，图纸退回草稿')
      onRefresh()
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      const msg = typeof detail === 'string' ? detail : detail?.message ?? '操作失败'
      message.error(msg)
    } finally {
      setLoading(false)
    }
  }

  if (!canReview) {
    return (
      <Alert
        type="info"
        showIcon
        message="一审（技术规范化）由项目总工负责，您当前角色无操作权限"
      />
    )
  }

  return (
    <Card title="一审 — 技术规范化审批" size="small">
      {aiReport && (
        <Alert
          style={{ marginBottom: 16 }}
          type={aiReport.critical_issues > 0 ? 'warning' : 'success'}
          showIcon
          message={
            `AI 审图：共 ${aiReport.total_issues} 个问题` +
            (aiReport.critical_issues > 0 ? `，其中 ${aiReport.critical_issues} 个严重问题` : '，无严重问题')
          }
        />
      )}

      <Form form={form} layout="vertical">
        <Form.Item name="ai_report_confirmed" valuePropName="checked" rules={[
          { validator: (_, v) => v ? Promise.resolve() : Promise.reject('必须确认 AI 审查报告') }
        ]}>
          <Checkbox>已确认 AI 审查报告</Checkbox>
        </Form.Item>
        <Form.Item name="bim_check_confirmed" valuePropName="checked">
          <Checkbox>已确认 BIM 碰撞检查</Checkbox>
        </Form.Item>
        <Form.Item name="issues_all_closed" valuePropName="checked">
          <Checkbox>所有 critical / major 问题已关闭或标注已知风险</Checkbox>
        </Form.Item>
        <Form.Item name="notes" label="审查意见">
          <Input.TextArea rows={3} placeholder="可选，填写审查意见或驳回原因" />
        </Form.Item>
      </Form>

      <Divider style={{ margin: '12px 0' }} />
      <Space>
        <Button
          type="primary"
          icon={<CheckOutlined />}
          loading={loading}
          onClick={() => submit('approved')}
        >
          通过一审 → 进入二审
        </Button>
        <Button
          danger
          icon={<CloseOutlined />}
          loading={loading}
          onClick={() => submit('rejected')}
        >
          驳回
        </Button>
      </Space>
    </Card>
  )
}
