/**
 * 条文列表（抽屉内使用）
 * - 按强条/义务等级过滤
 * - 手动新增 / 编辑 / 删除
 */
import { useRef, useState } from 'react'
import { ProTable, ProColumns, ActionType } from '@ant-design/pro-components'
import {
  Button, Modal, Form, Input, Select, Switch,
  Space, Tag, Popconfirm, message,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { listArticles, createArticle, updateArticle, deleteArticle } from '@/services/regulations'

type Article = {
  id: string
  article_no: string
  title: string | null
  content_preview: string
  obligation_level: string
  is_mandatory: boolean
  vector_id: string | null
  created_at: string
}

const OBL_CONFIG: Record<string, { label: string; color: string }> = {
  MUST:      { label: '必须',   color: 'red' },
  MUST_NOT:  { label: '严禁',   color: 'volcano' },
  SHOULD:    { label: '应',     color: 'blue' },
  MAY:       { label: '宜',     color: 'cyan' },
}

interface Props {
  bookId: string
}

export default function ArticleList({ bookId }: Props) {
  const actionRef = useRef<ActionType>()
  const [form] = Form.useForm()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Article | null>(null)
  const [saving, setSaving] = useState(false)

  const openCreate = () => { setEditing(null); form.resetFields(); setModalOpen(true) }
  const openEdit = (a: Article) => {
    setEditing(a)
    form.setFieldsValue({ ...a, is_mandatory: a.is_mandatory })
    setModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      setSaving(true)
      if (editing) {
        await updateArticle(bookId, editing.id, vals)
        message.success('已更新')
      } else {
        await createArticle(bookId, vals)
        message.success('已新增，自动触发向量化')
      }
      setModalOpen(false)
      actionRef.current?.reload()
    } catch {
      // form validation error — no-op
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    await deleteArticle(bookId, id)
    message.success('已删除')
    actionRef.current?.reload()
  }

  const columns: ProColumns<Article>[] = [
    {
      title: '条文号',
      dataIndex: 'article_no',
      width: 90,
      render: (v, row) => (
        <Space>
          {row.is_mandatory && <Tag color="red" style={{ fontSize: 10, padding: '0 4px' }}>强条</Tag>}
          <span style={{ fontFamily: 'monospace' }}>{v as string}</span>
        </Space>
      ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      width: 150,
      ellipsis: true,
      render: v => v ?? '—',
    },
    {
      title: '内容（摘要）',
      dataIndex: 'content_preview',
      ellipsis: true,
    },
    {
      title: '义务等级',
      dataIndex: 'obligation_level',
      width: 80,
      render: v => {
        const cfg = OBL_CONFIG[v as string] ?? { label: v, color: 'default' }
        return <Tag color={cfg.color}>{cfg.label}</Tag>
      },
    },
    {
      title: '向量化',
      dataIndex: 'vector_id',
      width: 70,
      search: false,
      render: v => v ? <Tag color="green">已完成</Tag> : <Tag>待处理</Tag>,
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
      <ProTable<Article>
        actionRef={actionRef}
        rowKey="id"
        size="small"
        columns={columns}
        request={async (params) => {
          const res = await listArticles(bookId, {
            q: params.article_no || params.content_preview,
            obligation_level: params.obligation_level,
            limit: params.pageSize ?? 50,
            offset: ((params.current ?? 1) - 1) * (params.pageSize ?? 50),
          })
          return { data: res.items ?? [], total: res.total ?? 0, success: true }
        }}
        toolBarRender={() => [
          <Button key="add" type="primary" size="small" icon={<PlusOutlined />} onClick={openCreate}>
            手动录入
          </Button>,
        ]}
        search={{ labelWidth: 'auto' }}
        pagination={{ pageSize: 30 }}
      />

      <Modal
        title={editing ? '编辑条文' : '手动录入条文'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={600}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="article_no" label="条文编号" rules={[{ required: true }]}>
            <Input placeholder="如：4.2.3" />
          </Form.Item>
          <Form.Item name="title" label="条文标题">
            <Input placeholder="如：钢筋保护层厚度" />
          </Form.Item>
          <Form.Item name="content" label="条文内容" rules={[{ required: true }]}>
            <Input.TextArea rows={5} placeholder="输入完整条文内容" />
          </Form.Item>
          <Form.Item name="obligation_level" label="义务等级" initialValue="SHOULD">
            <Select
              options={Object.entries(OBL_CONFIG).map(([k, v]) => ({ label: v.label, value: k }))}
            />
          </Form.Item>
          <Form.Item name="is_mandatory" label="强制性条文" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
