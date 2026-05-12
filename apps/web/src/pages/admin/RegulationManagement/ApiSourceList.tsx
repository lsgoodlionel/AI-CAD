/**
 * 外部 API 接入源管理
 */
import { useRef, useState } from 'react'
import { ProTable, ProColumns, ActionType } from '@ant-design/pro-components'
import {
  Button, Modal, Form, Input, InputNumber, Select,
  Space, Tag, Switch, Popconfirm, message,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import {
  listApiSources, createApiSource, updateApiSource, deleteApiSource,
} from '@/services/regulations'

type ApiSource = {
  id: string
  name: string
  endpoint_url: string
  auth_type: string
  sync_interval_hours: number
  last_synced_at: string | null
  is_active: boolean
  created_at: string
}

export default function ApiSourceList() {
  const actionRef = useRef<ActionType>()
  const [form] = Form.useForm()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ApiSource | null>(null)
  const [saving, setSaving] = useState(false)

  const openCreate = () => { setEditing(null); form.resetFields(); setModalOpen(true) }
  const openEdit   = (s: ApiSource) => { setEditing(s); form.setFieldsValue(s); setModalOpen(true) }

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      setSaving(true)
      if (editing) {
        await updateApiSource(editing.id, vals)
        message.success('已更新')
      } else {
        await createApiSource(vals)
        message.success('已创建')
      }
      setModalOpen(false)
      actionRef.current?.reload()
    } catch {
      // form validation error — no-op
    } finally {
      setSaving(false)
    }
  }

  const handleToggle = async (s: ApiSource, active: boolean) => {
    await updateApiSource(s.id, { is_active: active })
    message.success(active ? '已启用' : '已停用')
    actionRef.current?.reload()
  }

  const handleDelete = async (id: string) => {
    await deleteApiSource(id)
    message.success('已删除')
    actionRef.current?.reload()
  }

  const columns: ProColumns<ApiSource>[] = [
    { title: '名称', dataIndex: 'name', ellipsis: true },
    { title: '接口地址', dataIndex: 'endpoint_url', ellipsis: true },
    {
      title: '认证方式',
      dataIndex: 'auth_type',
      width: 100,
      render: v => <Tag>{v as string}</Tag>,
    },
    {
      title: '同步间隔',
      dataIndex: 'sync_interval_hours',
      width: 100,
      search: false,
      render: v => `${v} 小时`,
    },
    {
      title: '上次同步',
      dataIndex: 'last_synced_at',
      width: 150,
      search: false,
      render: v => v ? new Date(v as string).toLocaleString('zh-CN') : '未同步',
    },
    {
      title: '启用',
      dataIndex: 'is_active',
      width: 70,
      search: false,
      render: (_, row) => (
        <Switch
          size="small"
          checked={row.is_active}
          onChange={v => handleToggle(row, v)}
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      search: false,
      render: (_, row) => (
        <Space size={4}>
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
      <ProTable<ApiSource>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async () => {
          const res = await listApiSources()
          return { data: res.items ?? [], total: res.items?.length ?? 0, success: true }
        }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            添加接入源
          </Button>,
        ]}
        search={false}
        pagination={false}
      />

      <Modal
        title={editing ? '编辑接入源' : '添加外部 API 接入源'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={520}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：住建部规范开放平台" />
          </Form.Item>
          <Form.Item name="endpoint_url" label="接口地址" rules={[{ required: true }]}>
            <Input placeholder="https://api.example.com/regulations" />
          </Form.Item>
          <Form.Item name="auth_type" label="认证方式" initialValue="api_key">
            <Select
              options={[
                { label: 'API Key', value: 'api_key' },
                { label: 'OAuth 2.0', value: 'oauth' },
                { label: '无认证', value: 'none' },
              ]}
            />
          </Form.Item>
          <Form.Item name="sync_interval_hours" label="同步间隔（小时）" initialValue={24}>
            <InputNumber min={1} max={720} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
