/**
 * 提供商管理列表
 * - CRUD（新增/编辑/删除）
 * - 单个健康检查 + 全量健康检查
 * - 支持 4 种 provider_type：anthropic / openai_compat / ollama / custom_http
 */
import { useRef, useState, useCallback } from 'react'
import { ProTable, ProColumns, ActionType } from '@ant-design/pro-components'
import {
  Button, Modal, Form, Input, Select, InputNumber,
  Popconfirm, Space, Badge, Tag, message, Tooltip,
} from 'antd'
import {
  PlusOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import {
  listProviders, createProvider, updateProvider,
  deleteProvider, checkProviderHealth, checkAllHealth,
} from '@/services/modelManagement'

type Provider = {
  id: string
  name: string
  provider_type: string
  base_url: string | null
  api_key_env: string | null
  timeout_sec: number
  is_active: boolean
}

const TYPE_LABELS: Record<string, string> = {
  anthropic:     'Anthropic',
  openai_compat: 'OpenAI 兼容',
  ollama:        'Ollama 本地',
  custom_http:   '自定义 HTTP',
}

const TYPE_COLORS: Record<string, string> = {
  anthropic:     'purple',
  openai_compat: 'blue',
  ollama:        'green',
  custom_http:   'orange',
}

type HealthMap = Record<string, boolean>

export default function ProviderList() {
  const actionRef = useRef<ActionType>()
  const [form] = Form.useForm()
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [healthMap, setHealthMap] = useState<HealthMap>({})
  const [checkingId, setCheckingId] = useState<string | null>(null)
  const [checkingAll, setCheckingAll] = useState(false)

  const openCreate = () => {
    setEditingId(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (row: Provider) => {
    setEditingId(row.id)
    form.setFieldsValue(row)
    setModalOpen(true)
  }

  const handleSubmit = useCallback(async () => {
    const values = await form.validateFields()
    if (editingId) {
      await updateProvider(editingId, values)
      message.success('已更新')
    } else {
      await createProvider(values)
      message.success('已创建')
    }
    setModalOpen(false)
    actionRef.current?.reload()
  }, [editingId, form])

  const handleDelete = useCallback(async (id: string) => {
    await deleteProvider(id)
    message.success('已删除')
    actionRef.current?.reload()
  }, [])

  const handleCheck = useCallback(async (id: string, name: string) => {
    setCheckingId(id)
    const { healthy } = await checkProviderHealth(id)
    setHealthMap(prev => ({ ...prev, [name]: healthy }))
    setCheckingId(null)
    message.info(healthy ? `${name} 连通正常` : `${name} 连通失败`)
  }, [])

  const handleCheckAll = useCallback(async () => {
    setCheckingAll(true)
    const result = await checkAllHealth()
    setHealthMap(result)
    setCheckingAll(false)
  }, [])

  const columns: ProColumns<Provider>[] = [
    {
      title: '提供商',
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Space>
            {row.name in healthMap && (
              healthMap[row.name]
                ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                : <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
            )}
            <strong>{row.name}</strong>
          </Space>
          <Tag color={TYPE_COLORS[row.provider_type]}>
            {TYPE_LABELS[row.provider_type] ?? row.provider_type}
          </Tag>
        </Space>
      ),
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      ellipsis: true,
      render: v => v ?? <span style={{ color: '#aaa' }}>— (官方端点)</span>,
    },
    {
      title: 'API Key 环境变量',
      dataIndex: 'api_key_env',
      render: v => v ? <code style={{ fontSize: 12 }}>{v}</code> : <span style={{ color: '#aaa' }}>—</span>,
    },
    {
      title: '超时 (s)',
      dataIndex: 'timeout_sec',
      width: 90,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 80,
      render: v => <Badge status={v ? 'success' : 'default'} text={v ? '启用' : '停用'} />,
    },
    {
      title: '操作',
      width: 180,
      render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
          <Tooltip title="健康检查">
            <Button
              size="small" icon={<ThunderboltOutlined />}
              loading={checkingId === row.id}
              onClick={() => handleCheck(row.id, row.name)}
            />
          </Tooltip>
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(row.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <ProTable<Provider>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async () => {
          const data = await listProviders()
          return { data, success: true }
        }}
        toolbar={{
          actions: [
            <Button
              key="check-all" icon={<ThunderboltOutlined />}
              loading={checkingAll} onClick={handleCheckAll}
            >
              全量健康检查
            </Button>,
            <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              添加提供商
            </Button>,
          ],
        }}
        search={false}
        pagination={false}
        size="small"
      />

      <Modal
        title={editingId ? '编辑提供商' : '添加提供商'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        width={560}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="Claude API" />
          </Form.Item>
          <Form.Item name="provider_type" label="提供商类型" rules={[{ required: true }]}>
            <Select
              options={Object.entries(TYPE_LABELS).map(([v, l]) => ({ value: v, label: l }))}
              placeholder="选择类型"
            />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL">
            <Input placeholder="留空使用官方端点（Anthropic 默认）" />
          </Form.Item>
          <Form.Item
            name="api_key_env"
            label="API Key 环境变量名"
            extra="填写 OS 环境变量名（如 ANTHROPIC_API_KEY），系统运行时读取，不存储明文"
          >
            <Input placeholder="ANTHROPIC_API_KEY" />
          </Form.Item>
          <Form.Item name="timeout_sec" label="超时（秒）">
            <InputNumber min={10} max={600} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
