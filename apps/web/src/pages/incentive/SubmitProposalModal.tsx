import { useState } from 'react'
import { Modal, Form, Input, Select, InputNumber, Radio, message } from 'antd'
import { submitProposal } from '@/services/incentive'

interface Props {
  open: boolean
  onClose: () => void
  onSuccess: () => void
  defaultProjectId?: string
}

const TYPE_OPTIONS = [
  { value: 'A', label: 'A 类 — 设计变更节约（材料/工艺优化）' },
  { value: 'B', label: 'B 类 — 施工优化节约（方案/工序改进）' },
]

export default function SubmitProposalModal({ open, onClose, onSuccess, defaultProjectId }: Props) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  const handleOk = async () => {
    const values = await form.validateFields()
    setLoading(true)
    try {
      await submitProposal({
        project_id: values.project_id,
        drawing_id: values.drawing_id || undefined,
        proposal_type: values.proposal_type,
        title: values.title,
        description: values.description,
        raw_saving_est: values.raw_saving_est,
      })
      message.success('提案已提交，等待项目经理审核')
      form.resetFields()
      onSuccess()
      onClose()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '提交失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title="提交创效激励提案"
      open={open}
      onCancel={() => { form.resetFields(); onClose() }}
      onOk={handleOk}
      confirmLoading={loading}
      width={600}
      okText="提交提案"
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        initialValues={{ project_id: defaultProjectId, proposal_type: 'A' }}
      >
        <Form.Item name="proposal_type" label="提案类型" rules={[{ required: true }]}>
          <Radio.Group>
            {TYPE_OPTIONS.map(o => (
              <Radio.Button key={o.value} value={o.value} style={{ height: 'auto', padding: '8px 16px' }}>
                {o.label}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Form.Item>

        <Form.Item name="project_id" label="项目 ID" rules={[{ required: true }]}>
          <Input placeholder="项目 UUID（后续改为下拉选择）" />
        </Form.Item>

        <Form.Item name="drawing_id" label="关联图纸 ID（可选）">
          <Input placeholder="图纸 UUID（可不填）" />
        </Form.Item>

        <Form.Item name="title" label="提案标题" rules={[{ required: true, min: 2 }]}>
          <Input placeholder="简明描述优化措施，如：将现浇楼板改为叠合板减少支撑用钢量" />
        </Form.Item>

        <Form.Item name="description" label="详细说明" rules={[{ required: true, min: 10 }]}>
          <Input.TextArea
            rows={5}
            placeholder="详细描述优化方案、与原设计的对比、节约来源分析及技术可行性说明..."
          />
        </Form.Item>

        <Form.Item name="raw_saving_est" label="预估节约金额（元，可不填）">
          <InputNumber
            style={{ width: '100%' }}
            min={0}
            step={10000}
            formatter={v => v ? `¥ ${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : ''}
            placeholder="提案人估算值，以商务核算为准"
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
