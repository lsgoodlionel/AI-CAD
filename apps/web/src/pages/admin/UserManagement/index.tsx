import { useEffect, useRef, useState } from 'react'
import { ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import {
  Button, Form, Input, Modal, Popconfirm, Select, Space, Tabs, Tag, message,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import {
  createOrganization, createUser, disableUser, enableUser, listOrganizations,
  listUsers, resetUserPassword, updateOrganization, updateUser,
} from '@/services/adminUsers'

const ROLE_OPTIONS = [
  { label: '集团管理员', value: 'group_admin' },
  { label: '集团总工', value: 'group_chief_engineer' },
  { label: '深化总监', value: 'group_deepening_director' },
  { label: '商务总监', value: 'group_commercial_director' },
  { label: '项目经理', value: 'project_manager' },
  { label: '项目总工', value: 'project_chief_engineer' },
  { label: '经济师', value: 'economist' },
  { label: '设计人员', value: 'designer' },
  { label: '现场工程师', value: 'site_engineer' },
  { label: '班组成员', value: 'labor_crew' },
]

const ORG_TYPE_OPTIONS = [
  { label: '集团', value: 'group' },
  { label: '公司', value: 'company' },
  { label: '分公司', value: 'branch' },
  { label: '项目部', value: 'project_dept' },
]

type UserRow = {
  id: string
  username: string
  display_name: string
  role: string
  org_name?: string
  email?: string
  phone?: string
  position?: string
  is_active: boolean
  project_count: number
  last_login_at?: string
}

type OrgRow = {
  id: string
  name: string
  code?: string
  org_type: string
  parent_id?: string
  parent_name?: string
  user_count: number
  project_count: number
}

export default function UserManagement() {
  const userActionRef = useRef<ActionType>()
  const orgActionRef = useRef<ActionType>()
  const [userForm] = Form.useForm()
  const [orgForm] = Form.useForm()
  const [passwordForm] = Form.useForm()
  const [userOpen, setUserOpen] = useState(false)
  const [orgOpen, setOrgOpen] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState<UserRow | null>(null)
  const [editingUser, setEditingUser] = useState<UserRow | null>(null)
  const [editingOrg, setEditingOrg] = useState<OrgRow | null>(null)
  const [orgs, setOrgs] = useState<OrgRow[]>([])

  const refreshOrgs = async () => {
    const res = await listOrganizations()
    setOrgs(res.items ?? [])
    orgActionRef.current?.reload()
  }

  useEffect(() => { refreshOrgs() }, [])

  const openUser = (row?: UserRow) => {
    setEditingUser(row ?? null)
    userForm.resetFields()
    userForm.setFieldsValue(row ?? { role: 'designer', is_active: true })
    setUserOpen(true)
  }

  const saveUser = async () => {
    const values = await userForm.validateFields()
    if (editingUser) {
      await updateUser(editingUser.id, values)
      message.success('人员已更新')
    } else {
      await createUser(values)
      message.success('人员已创建')
    }
    setUserOpen(false)
    userActionRef.current?.reload()
  }

  const openOrg = (row?: OrgRow) => {
    setEditingOrg(row ?? null)
    orgForm.resetFields()
    orgForm.setFieldsValue(row ?? { org_type: 'company' })
    setOrgOpen(true)
  }

  const saveOrg = async () => {
    const values = await orgForm.validateFields()
    if (editingOrg) {
      await updateOrganization(editingOrg.id, values)
      message.success('组织已更新')
    } else {
      await createOrganization(values)
      message.success('组织已创建')
    }
    setOrgOpen(false)
    await refreshOrgs()
  }

  const userColumns: ProColumns<UserRow>[] = [
    { title: '姓名', dataIndex: 'display_name' },
    { title: '账号', dataIndex: 'username', copyable: true },
    {
      title: '角色',
      dataIndex: 'role',
      width: 130,
      valueEnum: Object.fromEntries(ROLE_OPTIONS.map(o => [o.value, { text: o.label }])),
      render: (_, row) => ROLE_OPTIONS.find(o => o.value === row.role)?.label ?? row.role,
    },
    { title: '组织', dataIndex: 'org_name', search: false },
    { title: '职位', dataIndex: 'position', search: false, width: 110 },
    { title: '项目数', dataIndex: 'project_count', search: false, width: 80 },
    {
      title: '状态',
      dataIndex: 'is_active',
      search: false,
      width: 80,
      render: (_, row) => row.is_active ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
    },
    {
      title: '操作',
      search: false,
      width: 220,
      render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => openUser(row)}>编辑</Button>
          <Button size="small" onClick={() => setPasswordOpen(row)}>重置密码</Button>
          {row.is_active ? (
            <Popconfirm title="停用该账号？" onConfirm={async () => { await disableUser(row.id); userActionRef.current?.reload() }}>
              <Button size="small" danger>停用</Button>
            </Popconfirm>
          ) : (
            <Button size="small" onClick={async () => { await enableUser(row.id); userActionRef.current?.reload() }}>启用</Button>
          )}
        </Space>
      ),
    },
  ]

  const orgColumns: ProColumns<OrgRow>[] = [
    { title: '组织名称', dataIndex: 'name' },
    { title: '编码', dataIndex: 'code', width: 120 },
    { title: '上级组织', dataIndex: 'parent_name', search: false },
    {
      title: '类型',
      dataIndex: 'org_type',
      width: 110,
      render: (_, row) => ORG_TYPE_OPTIONS.find(o => o.value === row.org_type)?.label ?? row.org_type,
    },
    { title: '人员数', dataIndex: 'user_count', search: false, width: 80 },
    { title: '项目数', dataIndex: 'project_count', search: false, width: 80 },
    { title: '操作', search: false, width: 90, render: (_, row) => <Button size="small" onClick={() => openOrg(row)}>编辑</Button> },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Tabs
        items={[
          {
            key: 'users',
            label: '人员管理',
            children: (
              <ProTable<UserRow>
                actionRef={userActionRef}
                rowKey="id"
                columns={userColumns}
                request={async params => {
                  const res = await listUsers({
                    keyword: params.display_name ?? params.username,
                    role: params.role,
                    limit: params.pageSize,
                    offset: ((params.current ?? 1) - 1) * (params.pageSize ?? 20),
                  })
                  return { data: res.items ?? [], total: res.total ?? 0, success: true }
                }}
                toolBarRender={() => [
                  <Button key="add" type="primary" icon={<PlusOutlined />} onClick={() => openUser()}>新增人员</Button>,
                ]}
              />
            ),
          },
          {
            key: 'orgs',
            label: '组织架构',
            children: (
              <ProTable<OrgRow>
                actionRef={orgActionRef}
                rowKey="id"
                columns={orgColumns}
                dataSource={orgs}
                search={false}
                toolBarRender={() => [
                  <Button key="add" type="primary" icon={<PlusOutlined />} onClick={() => openOrg()}>新增组织</Button>,
                ]}
              />
            ),
          },
        ]}
      />

      <Modal title={editingUser ? '编辑人员' : '新增人员'} open={userOpen} onOk={saveUser} onCancel={() => setUserOpen(false)} width={680}>
        <Form form={userForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="org_id" label="所属组织">
            <Select allowClear showSearch optionFilterProp="label" options={orgs.map(o => ({ label: o.name, value: o.id }))} />
          </Form.Item>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="username" label="账号" rules={[{ required: true }]} style={{ width: '50%' }}>
              <Input disabled={!!editingUser} />
            </Form.Item>
            <Form.Item name="display_name" label="姓名" rules={[{ required: true }]} style={{ width: '50%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
          {!editingUser && (
            <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6 }]}>
              <Input.Password />
            </Form.Item>
          )}
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="role" label="角色" rules={[{ required: true }]} style={{ width: '50%' }}>
              <Select options={ROLE_OPTIONS} />
            </Form.Item>
            <Form.Item name="position" label="职位" style={{ width: '50%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="email" label="邮箱" style={{ width: '50%' }}>
              <Input />
            </Form.Item>
            <Form.Item name="phone" label="手机" style={{ width: '50%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
        </Form>
      </Modal>

      <Modal title={editingOrg ? '编辑组织' : '新增组织'} open={orgOpen} onOk={saveOrg} onCancel={() => setOrgOpen(false)}>
        <Form form={orgForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="组织名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label="组织编码">
            <Input />
          </Form.Item>
          <Form.Item name="parent_id" label="上级组织">
            <Select allowClear showSearch optionFilterProp="label" options={orgs.filter(o => o.id !== editingOrg?.id).map(o => ({ label: o.name, value: o.id }))} />
          </Form.Item>
          <Form.Item name="org_type" label="组织类型" rules={[{ required: true }]}>
            <Select options={ORG_TYPE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`重置密码 — ${passwordOpen?.display_name ?? ''}`}
        open={!!passwordOpen}
        onCancel={() => { setPasswordOpen(null); passwordForm.resetFields() }}
        onOk={async () => {
          const values = await passwordForm.validateFields()
          await resetUserPassword(passwordOpen!.id, values.password)
          message.success('密码已重置')
          setPasswordOpen(null)
          passwordForm.resetFields()
        }}
      >
        <Form form={passwordForm} layout="vertical">
          <Form.Item name="password" label="新密码" rules={[{ required: true, min: 6 }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
