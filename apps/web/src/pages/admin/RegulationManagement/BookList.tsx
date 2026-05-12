/**
 * 规范文件列表
 * - 新建 / 编辑 / 删除 / 发布 / 下线
 * - 文件导入（PDF/Word/Excel → NLP 流水线）
 * - 点击展开条文列表
 */
import { useRef, useState } from 'react'
import { ProTable, ProColumns, ActionType } from '@ant-design/pro-components'
import {
  Button, Modal, Form, Input, Select, Upload, Space, Tag,
  Popconfirm, message, Drawer, Badge,
} from 'antd'
import {
  PlusOutlined, UploadOutlined, CheckOutlined,
  StopOutlined, InboxOutlined,
} from '@ant-design/icons'
import {
  listBooks, createBook, updateBook, deleteBook,
  publishBook, unpublishBook, importBookFile,
} from '@/services/regulations'
import ArticleList from './ArticleList'

type Book = {
  id: string
  title: string
  std_no: string | null
  version: string | null
  discipline: string | null
  status: string
  source_type: string
  article_count: number
  created_at: string
  updated_at: string
}

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  draft:         { label: '草稿',   color: 'default' },
  processing:    { label: '导入中', color: 'processing' },
  active:        { label: '已发布', color: 'success' },
  import_failed: { label: '导入失败', color: 'error' },
  superseded:    { label: '已废止', color: 'orange' },
  withdrawn:     { label: '已撤回', color: 'red' },
}

const DISCIPLINE_OPTIONS = [
  { label: '通用',     value: 'general' },
  { label: '结构',     value: 'structure' },
  { label: '建筑',     value: 'architecture' },
  { label: '机电',     value: 'mep' },
  { label: '消防',     value: 'fire' },
  { label: '装修',     value: 'decoration' },
]

export default function BookList() {
  const actionRef = useRef<ActionType>()
  const [form] = Form.useForm()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Book | null>(null)
  const [importTarget, setImportTarget] = useState<Book | null>(null)
  const [drawerBook, setDrawerBook] = useState<Book | null>(null)
  const [saving, setSaving] = useState(false)
  const [importing, setImporting] = useState(false)

  const openCreate = () => { setEditing(null); form.resetFields(); setModalOpen(true) }
  const openEdit   = (b: Book) => { setEditing(b); form.setFieldsValue(b); setModalOpen(true) }

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      setSaving(true)
      if (editing) {
        await updateBook(editing.id, vals)
        message.success('已更新')
      } else {
        await createBook(vals)
        message.success('已新建，请上传文件或手动录入条文')
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
    await deleteBook(id)
    message.success('已删除')
    actionRef.current?.reload()
  }

  const handlePublish = async (b: Book) => {
    try {
      await publishBook(b.id)
      message.success('已发布')
      actionRef.current?.reload()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '发布失败')
    }
  }

  const handleUnpublish = async (b: Book) => {
    await unpublishBook(b.id)
    message.success('已下线')
    actionRef.current?.reload()
  }

  const handleImport = async (file: File) => {
    if (!importTarget) return false
    setImporting(true)
    try {
      await importBookFile(importTarget.id, file)
      message.success('文件已上传，NLP 导入任务已启动，请稍后刷新')
      actionRef.current?.reload()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '上传失败')
    } finally {
      setImporting(false)
      setImportTarget(null)
    }
    return false
  }

  const columns: ProColumns<Book>[] = [
    {
      title: '规范编号',
      dataIndex: 'std_no',
      width: 140,
      render: v => v ?? <span style={{ color: '#999' }}>—</span>,
    },
    {
      title: '名称',
      dataIndex: 'title',
      ellipsis: true,
      render: (v, row) => (
        <a onClick={() => setDrawerBook(row)}>{v as string}</a>
      ),
    },
    {
      title: '专业',
      dataIndex: 'discipline',
      width: 80,
      render: v => v ? DISCIPLINE_OPTIONS.find(o => o.value === v)?.label ?? v : '—',
    },
    {
      title: '版本',
      dataIndex: 'version',
      width: 90,
      render: v => v ?? '—',
    },
    {
      title: '条文数',
      dataIndex: 'article_count',
      width: 80,
      search: false,
      render: v => <Badge count={v as number} showZero color="#1677ff" />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      valueEnum: Object.fromEntries(
        Object.entries(STATUS_CONFIG).map(([k, v]) => [k, { text: v.label }])
      ),
      render: (_, row) => {
        const cfg = STATUS_CONFIG[row.status] ?? { label: row.status, color: 'default' }
        return <Tag color={cfg.color}>{cfg.label}</Tag>
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      search: false,
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
          <Button
            size="small"
            icon={<UploadOutlined />}
            onClick={() => setImportTarget(row)}
          >
            导入文件
          </Button>
          {row.status === 'draft' || row.status === 'import_failed' ? (
            <Button
              size="small"
              type="primary"
              icon={<CheckOutlined />}
              onClick={() => handlePublish(row)}
            >
              发布
            </Button>
          ) : row.status === 'active' ? (
            <Popconfirm title="确认下线？" onConfirm={() => handleUnpublish(row)}>
              <Button size="small" danger icon={<StopOutlined />}>下线</Button>
            </Popconfirm>
          ) : null}
          <Popconfirm title="确认删除？此操作将同时删除所有条文。" onConfirm={() => handleDelete(row.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <ProTable<Book>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async (params) => {
          const res = await listBooks({
            discipline: params.discipline,
            status: params.status,
            limit: params.pageSize ?? 50,
            offset: ((params.current ?? 1) - 1) * (params.pageSize ?? 50),
          })
          return { data: res.items ?? [], total: res.total ?? 0, success: true }
        }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建规范
          </Button>,
        ]}
        search={{ labelWidth: 'auto' }}
        pagination={{ pageSize: 20 }}
      />

      {/* 新建 / 编辑弹窗 */}
      <Modal
        title={editing ? '编辑规范文件' : '新建规范文件'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={520}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="title" label="规范名称" rules={[{ required: true }]}>
            <Input placeholder="如：建筑设计防火规范" />
          </Form.Item>
          <Form.Item name="std_no" label="规范编号">
            <Input placeholder="如：GB50016-2014（2018年版）" />
          </Form.Item>
          <Form.Item name="version" label="版本">
            <Input placeholder="如：2018年版" />
          </Form.Item>
          <Form.Item name="discipline" label="专业分类">
            <Select options={DISCIPLINE_OPTIONS} placeholder="选择专业" allowClear />
          </Form.Item>
          <Form.Item name="publisher" label="发布机构">
            <Input placeholder="如：住房和城乡建设部" />
          </Form.Item>
          <Form.Item name="effective_at" label="实施日期">
            <Input placeholder="如：2018-10-01" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 文件导入弹窗 */}
      <Modal
        title={`导入规范文件 — ${importTarget?.title ?? ''}`}
        open={!!importTarget}
        footer={null}
        onCancel={() => setImportTarget(null)}
        width={480}
      >
        <Upload.Dragger
          accept=".pdf,.docx,.doc,.xlsx"
          beforeUpload={handleImport}
          showUploadList={false}
          disabled={importing}
          style={{ padding: 24 }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
          <p className="ant-upload-hint">支持 PDF / Word / Excel，上传后自动触发 NLP 提取流水线</p>
        </Upload.Dragger>
      </Modal>

      {/* 条文列表侧边抽屉 */}
      <Drawer
        title={`条文列表 — ${drawerBook?.title ?? ''}`}
        open={!!drawerBook}
        onClose={() => setDrawerBook(null)}
        width={720}
        destroyOnClose
      >
        {drawerBook && <ArticleList bookId={drawerBook.id} />}
      </Drawer>
    </>
  )
}
