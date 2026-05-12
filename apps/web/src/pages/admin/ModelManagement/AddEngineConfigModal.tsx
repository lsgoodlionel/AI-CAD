/**
 * 新增引擎配置弹窗
 * 字段：引擎名称（13个预定义）× 任务类型 × 关联模型 × 推理参数
 */
import { useEffect, useState } from 'react'
import {
  Modal, Form, Select, Slider, InputNumber, Row, Col, message,
} from 'antd'
import {
  listModels, listEngineNames, createEngineConfig,
} from '@/services/modelManagement'

type Model = {
  id: string
  display_name: string
  model_id: string
  provider_name: string
  supports_vision: boolean
}

type Props = {
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

const TASK_TYPES = [
  { value: 'primary',    label: '主模型 (primary)' },
  { value: 'fallback_1', label: '备用1 (fallback_1)' },
  { value: 'fallback_2', label: '备用2 (fallback_2)' },
  { value: 'batch',      label: '批量 (batch)' },
]

export default function AddEngineConfigModal({ open, onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const [models, setModels] = useState<Model[]>([])
  const [engineNames, setEngineNames] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) return
    Promise.all([listModels(), listEngineNames()]).then(([ms, names]) => {
      setModels(ms)
      setEngineNames(names)
    })
  }, [open])

  const handleOk = async () => {
    const values = await form.validateFields()
    setSubmitting(true)
    try {
      await createEngineConfig(values)
      message.success('引擎配置已创建，路由器 30s 内生效')
      form.resetFields()
      onSuccess()
    } finally {
      setSubmitting(false)
    }
  }

  const modelOptions = models.map(m => ({
    value: m.id,
    label: `${m.display_name} [${m.provider_name}]`,
  }))

  return (
    <Modal
      title="添加引擎配置"
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      okText="创建"
      confirmLoading={submitting}
      width={620}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ task_type: 'primary', temperature: 0.1, max_tokens: 2048, top_p: 1.0, frequency_penalty: 0.0 }}
        style={{ marginTop: 16 }}
      >
        <Row gutter={16}>
          <Col span={14}>
            <Form.Item name="engine_name" label="引擎名称" rules={[{ required: true }]}>
              <Select
                showSearch
                placeholder="选择引擎"
                options={engineNames.map(n => ({ value: n, label: n }))}
                filterOption={(input, opt) =>
                  (opt?.label as string).toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>
          </Col>
          <Col span={10}>
            <Form.Item name="task_type" label="任务类型" rules={[{ required: true }]}>
              <Select options={TASK_TYPES} />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item name="model_id" label="关联模型" rules={[{ required: true }]}>
          <Select
            showSearch
            placeholder="选择模型"
            options={modelOptions}
            filterOption={(input, opt) =>
              (opt?.label as string).toLowerCase().includes(input.toLowerCase())
            }
          />
        </Form.Item>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="temperature" label={`温度: ${form.getFieldValue('temperature') ?? 0.1}`}>
              <Slider min={0} max={2} step={0.05}
                onChange={() => form.setFieldValue('temperature', form.getFieldValue('temperature'))}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="top_p" label={`Top-P: ${form.getFieldValue('top_p') ?? 1.0}`}>
              <Slider min={0} max={1} step={0.05}
                onChange={() => form.setFieldValue('top_p', form.getFieldValue('top_p'))}
              />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="max_tokens" label="Max Tokens">
              <InputNumber min={1} max={32000} step={256} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="frequency_penalty" label="Frequency Penalty">
              <InputNumber min={-2} max={2} step={0.1} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>
      </Form>
    </Modal>
  )
}
