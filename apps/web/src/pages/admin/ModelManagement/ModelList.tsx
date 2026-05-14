/**
 * 模型列表
 * - 按提供商筛选
 * - CRUD（新增/编辑/删除）
 * - 显示上下文窗口、视觉支持、输入/输出价格
 */
import { useRef, useState, useCallback, useEffect } from 'react'
import { ProTable, ProColumns, ActionType } from '@ant-design/pro-components'
import {
  Button, Modal, Form, Input, Select, InputNumber,
  Popconfirm, Space, Badge, Tag, message, Switch,
} from 'antd'
import { PlusOutlined, EyeOutlined } from '@ant-design/icons'
import {
  listProviders, listModels, createModel, updateModel, deleteModel,
  listProviderAvailableModels,
} from '@/services/modelManagement'

type Model = {
  id: string
  model_id: string
  display_name: string
  provider_id: string
  provider_name: string
  provider_type: string
  context_window: number | null
  supports_vision: boolean
  input_price_per_1m: number
  output_price_per_1m: number
  benchmark_score: number | null
  is_active: boolean
}

type Provider = { id: string; name: string; provider_type: string }
type AvailableModel = {
  model_id: string
  name: string
  size?: number
  details?: { parameter_size?: string; quantization_level?: string; family?: string }
}

export default function ModelList() {
  const actionRef = useRef<ActionType>()
  const [form] = Form.useForm()
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [providers, setProviders] = useState<Provider[]>([])
  const [filterProvider, setFilterProvider] = useState<string | undefined>()
  const [selectedProviderId, setSelectedProviderId] = useState<string | undefined>()
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([])
  const [loadingAvailableModels, setLoadingAvailableModels] = useState(false)

  useEffect(() => {
    listProviders().then(setProviders)
  }, [])

  const openCreate = () => {
    setEditingId(null)
    form.resetFields()
    setSelectedProviderId(undefined)
    setAvailableModels([])
    setModalOpen(true)
  }

  const openEdit = (row: Model) => {
    setEditingId(row.id)
    form.setFieldsValue({
      ...row,
      provider_id: row.provider_id,
    })
    setSelectedProviderId(row.provider_id)
    setModalOpen(true)
  }

  const selectedProvider = providers.find(p => p.id === selectedProviderId)
  const selectedProviderIsOllama = selectedProvider?.provider_type === 'ollama'

  useEffect(() => {
    if (!selectedProviderId || !selectedProviderIsOllama) {
      setAvailableModels([])
      return
    }
    setLoadingAvailableModels(true)
    listProviderAvailableModels(selectedProviderId)
      .then(res => setAvailableModels(res.models ?? []))
      .catch(() => {
        message.error('读取 Ollama 已安装模型失败')
        setAvailableModels([])
      })
      .finally(() => setLoadingAvailableModels(false))
  }, [selectedProviderId, selectedProviderIsOllama])

  const handleSubmit = useCallback(async () => {
    const values = await form.validateFields()
    if (editingId) {
      await updateModel(editingId, values)
      message.success('已更新')
    } else {
      await createModel(values)
      message.success('已创建')
    }
    setModalOpen(false)
    actionRef.current?.reload()
  }, [editingId, form])

  const handleDelete = useCallback(async (id: string) => {
    await deleteModel(id)
    message.success('已删除')
    actionRef.current?.reload()
  }, [])

  const columns: ProColumns<Model>[] = [
    {
      title: '模型',
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Space>
            <strong>{row.display_name}</strong>
            {row.supports_vision && (
              <Tag icon={<EyeOutlined />} color="purple">视觉</Tag>
            )}
          </Space>
          <code style={{ fontSize: 11, color: '#888' }}>{row.model_id}</code>
        </Space>
      ),
    },
    {
      title: '提供商',
      dataIndex: 'provider_name',
      width: 130,
      render: (v, row) => (
        <Space direction="vertical" size={1}>
          <span>{v}</span>
          <span style={{ fontSize: 11, color: '#aaa' }}>{row.provider_type}</span>
        </Space>
      ),
    },
    {
      title: '上下文窗口',
      dataIndex: 'context_window',
      width: 120,
      align: 'right',
      render: v => v ? `${(v / 1000).toFixed(0)}K` : '—',
    },
    {
      title: '输入价格 ($/1M)',
      dataIndex: 'input_price_per_1m',
      width: 130,
      align: 'right',
      render: v => v === 0 ? <span style={{ color: '#aaa' }}>本地免费</span> : `$${v}`,
    },
    {
      title: '输出价格 ($/1M)',
      dataIndex: 'output_price_per_1m',
      width: 130,
      align: 'right',
      render: v => v === 0 ? <span style={{ color: '#aaa' }}>本地免费</span> : `$${v}`,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 80,
      render: (v, row) => (
        <Badge
          status={v ? 'success' : 'default'}
          text={v ? '激活' : '停用'}
          onClick={async () => {
            await updateModel(row.id, { is_active: !v })
            actionRef.current?.reload()
          }}
          style={{ cursor: 'pointer' }}
        />
      ),
    },
    {
      title: '操作',
      width: 130,
      render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(row.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <ProTable<Model>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async () => {
          const data = await listModels(filterProvider)
          return { data, success: true }
        }}
        toolbar={{
          filter: (
            <Select
              style={{ width: 200 }}
              placeholder="全部提供商"
              allowClear
              value={filterProvider}
              onChange={v => {
                setFilterProvider(v)
                actionRef.current?.reload()
              }}
              options={providers.map(p => ({ value: p.id, label: p.name }))}
            />
          ),
          actions: [
            <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              添加模型
            </Button>,
          ],
        }}
        search={false}
        pagination={false}
        size="small"
      />

      <Modal
        title={editingId ? '编辑模型' : '添加模型'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        width={560}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="provider_id" label="所属提供商" rules={[{ required: true }]}>
            <Select
              options={providers.map(p => ({ value: p.id, label: p.name }))}
              placeholder="选择提供商"
              onChange={value => {
                setSelectedProviderId(value)
                form.setFieldsValue({ model_id: undefined, display_name: undefined })
              }}
            />
          </Form.Item>
          <Form.Item
            name="model_id"
            label="模型 ID"
            rules={[{ required: true }]}
            extra={
              selectedProviderIsOllama
                ? '来自 Ollama /api/tags 的本地已安装模型'
                : '如 claude-sonnet-4-6、gpt-4o、deepseek-chat'
            }
          >
            {selectedProviderIsOllama ? (
              <Select
                showSearch
                loading={loadingAvailableModels}
                placeholder="选择本地已安装模型"
                notFoundContent={loadingAvailableModels ? '读取中...' : '未读取到已安装模型'}
                options={availableModels.map(m => ({
                  value: m.model_id,
                  label: `${m.name}${m.details?.parameter_size ? ` · ${m.details.parameter_size}` : ''}${m.details?.quantization_level ? ` · ${m.details.quantization_level}` : ''}`,
                }))}
                onChange={value => {
                  const model = availableModels.find(m => m.model_id === value)
                  form.setFieldsValue({ display_name: model?.name ?? value })
                }}
                filterOption={(input, opt) =>
                  String(opt?.label ?? '').toLowerCase().includes(input.toLowerCase())
                }
              />
            ) : (
              <Input placeholder="模型 API 标识符" />
            )}
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}>
            <Input placeholder="Claude Sonnet 4.6" />
          </Form.Item>
          <Form.Item name="context_window" label="上下文窗口 (tokens)">
            <InputNumber min={1000} style={{ width: '100%' }} placeholder="200000" />
          </Form.Item>
          <Form.Item name="supports_vision" label="支持视觉输入" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="input_price_per_1m" label="输入价格 (USD/百万 token)">
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} placeholder="3.0（本地模型填 0）" />
          </Form.Item>
          <Form.Item name="output_price_per_1m" label="输出价格 (USD/百万 token)">
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} placeholder="15.0（本地模型填 0）" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
