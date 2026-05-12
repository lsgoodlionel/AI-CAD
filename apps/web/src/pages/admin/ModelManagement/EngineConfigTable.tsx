/**
 * 引擎模型配置表
 * 每行 = 一个引擎 × 一个任务类型（primary / fallback_1 / fallback_2 / batch）
 * 支持行内直接编辑温度、max_tokens 等推理参数，变更后路由器 30s 内热更新。
 */
import { useEffect, useState, useCallback } from 'react'
import {
  ProTable, ProColumns, ActionType,
} from '@ant-design/pro-components'
import {
  Button, Popconfirm, Select, Slider, InputNumber,
  Tag, Space, message, Tooltip, Badge,
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined,
  ThunderboltOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { useRef } from 'react'
import AddEngineConfigModal from './AddEngineConfigModal'
import {
  listEngineConfigs, updateEngineConfig, deleteEngineConfig,
} from '@/services/modelManagement'

const TASK_TYPE_COLOR: Record<string, string> = {
  primary:    'blue',
  fallback_1: 'orange',
  fallback_2: 'red',
  batch:      'green',
}

const TASK_TYPE_LABEL: Record<string, string> = {
  primary:    '主模型',
  fallback_1: '备用1',
  fallback_2: '备用2',
  batch:      '批量',
}

type EngineConfig = {
  id: string
  engine_name: string
  task_type: string
  is_enabled: boolean
  temperature: number
  max_tokens: number
  top_p: number
  frequency_penalty: number
  model_id: string
  display_name: string
  provider_name: string
  provider_type: string
  input_price_per_1m: number
  output_price_per_1m: number
  prompt_template_version: string | null
}

export default function EngineConfigTable() {
  const actionRef = useRef<ActionType>()
  const [addOpen, setAddOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValues, setEditValues] = useState<Partial<EngineConfig>>({})

  const handleSave = useCallback(async (id: string) => {
    await updateEngineConfig(id, editValues)
    message.success('已更新，路由器 30s 内生效')
    setEditingId(null)
    setEditValues({})
    actionRef.current?.reload()
  }, [editValues])

  const handleDelete = useCallback(async (id: string) => {
    await deleteEngineConfig(id)
    message.success('已删除')
    actionRef.current?.reload()
  }, [])

  const handleToggle = useCallback(async (id: string, current: boolean) => {
    await updateEngineConfig(id, { is_enabled: !current })
    message.success(!current ? '已启用' : '已禁用')
    actionRef.current?.reload()
  }, [])

  const columns: ProColumns<EngineConfig>[] = [
    {
      title: '引擎',
      dataIndex: 'engine_name',
      width: 200,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>{row.engine_name}</span>
          <Tag color={TASK_TYPE_COLOR[row.task_type]}>
            {TASK_TYPE_LABEL[row.task_type]}
          </Tag>
        </Space>
      ),
    },
    {
      title: '当前模型',
      dataIndex: 'display_name',
      render: (_, row) => (
        <Space>
          <Badge
            status={row.is_enabled ? 'processing' : 'default'}
            text={
              <span>
                <strong>{row.display_name}</strong>
                <span style={{ color: '#999', marginLeft: 6, fontSize: 12 }}>
                  [{row.provider_name}]
                </span>
              </span>
            }
          />
        </Space>
      ),
    },
    {
      title: '温度',
      dataIndex: 'temperature',
      width: 160,
      render: (_, row) =>
        editingId === row.id ? (
          <Slider
            min={0} max={2} step={0.05} style={{ width: 120 }}
            defaultValue={row.temperature}
            onChange={v => setEditValues(prev => ({ ...prev, temperature: v }))}
          />
        ) : (
          <span>{row.temperature}</span>
        ),
    },
    {
      title: 'Max Tokens',
      dataIndex: 'max_tokens',
      width: 130,
      render: (_, row) =>
        editingId === row.id ? (
          <InputNumber
            min={1} max={32000} step={256}
            defaultValue={row.max_tokens}
            style={{ width: 110 }}
            onChange={v => setEditValues(prev => ({ ...prev, max_tokens: v ?? row.max_tokens }))}
          />
        ) : (
          <span>{row.max_tokens.toLocaleString()}</span>
        ),
    },
    {
      title: 'Top-P',
      dataIndex: 'top_p',
      width: 120,
      render: (_, row) =>
        editingId === row.id ? (
          <Slider
            min={0} max={1} step={0.05} style={{ width: 90 }}
            defaultValue={row.top_p}
            onChange={v => setEditValues(prev => ({ ...prev, top_p: v }))}
          />
        ) : (
          <span>{row.top_p}</span>
        ),
    },
    {
      title: '单价 ($/1M)',
      width: 140,
      render: (_, row) => (
        <Tooltip title={`输入: $${row.input_price_per_1m} / 输出: $${row.output_price_per_1m}`}>
          <span style={{ color: '#888', fontSize: 12 }}>
            in ${row.input_price_per_1m} / out ${row.output_price_per_1m}
          </span>
        </Tooltip>
      ),
    },
    {
      title: '操作',
      width: 160,
      render: (_, row) =>
        editingId === row.id ? (
          <Space>
            <Button type="primary" size="small" onClick={() => handleSave(row.id)}>保存</Button>
            <Button size="small" onClick={() => { setEditingId(null); setEditValues({}) }}>取消</Button>
          </Space>
        ) : (
          <Space>
            <Tooltip title="编辑参数">
              <Button
                icon={<EditOutlined />} size="small"
                onClick={() => { setEditingId(row.id); setEditValues({}) }}
              />
            </Tooltip>
            <Tooltip title={row.is_enabled ? '禁用' : '启用'}>
              <Button
                icon={<ThunderboltOutlined />} size="small"
                type={row.is_enabled ? 'default' : 'primary'}
                onClick={() => handleToggle(row.id, row.is_enabled)}
              />
            </Tooltip>
            <Popconfirm title="确认删除？" onConfirm={() => handleDelete(row.id)}>
              <Button icon={<DeleteOutlined />} size="small" danger />
            </Popconfirm>
          </Space>
        ),
    },
  ]

  return (
    <>
      <ProTable<EngineConfig>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async params => {
          const data = await listEngineConfigs(params.engine_name)
          return { data, success: true }
        }}
        toolbar={{
          actions: [
            <Button
              key="reload" icon={<ReloadOutlined />}
              onClick={() => actionRef.current?.reload()}
            >
              刷新
            </Button>,
            <Button
              key="add" type="primary" icon={<PlusOutlined />}
              onClick={() => setAddOpen(true)}
            >
              添加引擎配置
            </Button>,
          ],
        }}
        search={false}
        pagination={false}
        size="small"
      />
      <AddEngineConfigModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSuccess={() => { setAddOpen(false); actionRef.current?.reload() }}
      />
    </>
  )
}
