import { useEffect, useRef, useState } from 'react'
import { ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import {
  Button, Drawer, Form, Input, InputNumber, Modal, Popconfirm, Select,
  Space, Table, Tabs, Tag, message,
} from 'antd'
import { PlusOutlined, TeamOutlined } from '@ant-design/icons'
import {
  addProjectMember, archiveProject, createProject, createWorkZone,
  listProjectMembers, listProjects, listWorkZones, removeProjectMember, updateProject,
} from '@/services/projects'
import { listOrganizations, listUsers } from '@/services/adminUsers'

const STATUS_OPTIONS = [
  { label: '进行中', value: 'active' },
  { label: '暂停', value: 'paused' },
  { label: '已完成', value: 'completed' },
]

const PROJECT_ROLE_OPTIONS = [
  { label: '项目经理', value: 'project_manager' },
  { label: '项目总工', value: 'project_chief_engineer' },
  { label: '商务负责人', value: 'commercial_manager' },
  { label: '经济师', value: 'economist' },
  { label: '设计人员', value: 'designer' },
  { label: '现场工程师', value: 'site_engineer' },
  { label: '班组成员', value: 'labor_crew' },
  { label: '查看者', value: 'viewer' },
]

const statusText: Record<string, string> = {
  active: '进行中',
  paused: '暂停',
  completed: '已完成',
}

type Project = {
  id: string
  name: string
  code?: string
  project_type?: string
  annual_output?: number
  status: string
  org_name: string
  manager_name?: string
  member_count: number
  drawing_count: number
}

export default function ProjectManagement() {
  const actionRef = useRef<ActionType>()
  const [form] = Form.useForm()
  const [memberForm] = Form.useForm()
  const [zoneForm] = Form.useForm()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [drawerProject, setDrawerProject] = useState<Project | null>(null)
  const [members, setMembers] = useState<any[]>([])
  const [zones, setZones] = useState<any[]>([])
  const [users, setUsers] = useState<any[]>([])
  const [orgs, setOrgs] = useState<any[]>([])

  const loadOptions = async () => {
    const [userRes, orgRes] = await Promise.all([listUsers({ limit: 200 }), listOrganizations()])
    setUsers(userRes.items ?? [])
    setOrgs(orgRes.items ?? [])
  }

  useEffect(() => { loadOptions() }, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ status: 'active' })
    setOpen(true)
  }

  const openEdit = (row: Project) => {
    setEditing(row)
    form.setFieldsValue(row)
    setOpen(true)
  }

  const saveProject = async () => {
    const values = await form.validateFields()
    if (editing) {
      await updateProject(editing.id, values)
      message.success('项目已更新')
    } else {
      await createProject(values)
      message.success('项目已创建')
    }
    setOpen(false)
    actionRef.current?.reload()
  }

  const loadDrawer = async (project: Project) => {
    setDrawerProject(project)
    const [memberRes, zoneRes] = await Promise.all([
      listProjectMembers(project.id),
      listWorkZones(project.id),
    ])
    setMembers(memberRes.items ?? [])
    setZones(zoneRes.items ?? [])
  }

  const columns: ProColumns<Project>[] = [
    { title: '项目名称', dataIndex: 'name', copyable: true },
    { title: '项目编码', dataIndex: 'code', width: 130 },
    { title: '类型', dataIndex: 'project_type', width: 100, search: false },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      valueEnum: Object.fromEntries(STATUS_OPTIONS.map(o => [o.value, { text: o.label }])),
      render: (_, row) => <Tag>{statusText[row.status] ?? row.status}</Tag>,
    },
    {
      title: '年产值',
      dataIndex: 'annual_output',
      width: 120,
      search: false,
      render: (_, row) => row.annual_output ? `¥${(Number(row.annual_output) / 10000).toFixed(0)}万` : '—',
    },
    { title: '组织', dataIndex: 'org_name', search: false },
    { title: '负责人', dataIndex: 'manager_name', search: false, width: 100 },
    { title: '成员', dataIndex: 'member_count', search: false, width: 70 },
    { title: '图纸', dataIndex: 'drawing_count', search: false, width: 70 },
    {
      title: '操作',
      width: 210,
      search: false,
      render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
          <Button size="small" icon={<TeamOutlined />} onClick={() => loadDrawer(row)}>成员/分区</Button>
          <Popconfirm title="归档项目？" onConfirm={async () => { await archiveProject(row.id); actionRef.current?.reload() }}>
            <Button size="small" danger>归档</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <ProTable<Project>
        actionRef={actionRef}
        rowKey="id"
        headerTitle="项目管理"
        columns={columns}
        request={async params => {
          const res = await listProjects({
            keyword: params.name,
            status: params.status,
            limit: params.pageSize,
            offset: ((params.current ?? 1) - 1) * (params.pageSize ?? 20),
          })
          return { data: res.items ?? [], total: res.total ?? 0, success: true }
        }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建项目</Button>,
        ]}
      />

      <Modal title={editing ? '编辑项目' : '新建项目'} open={open} onOk={saveProject} onCancel={() => setOpen(false)} width={720}>
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="org_id" label="所属组织" rules={[{ required: true }]}>
            <Select options={orgs.map(o => ({ label: o.name, value: o.id }))} showSearch optionFilterProp="label" />
          </Form.Item>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="name" label="项目名称" rules={[{ required: true }]} style={{ width: '55%' }}>
              <Input />
            </Form.Item>
            <Form.Item name="code" label="项目编码" style={{ width: '45%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="project_type" label="项目类型" style={{ width: '35%' }}>
              <Input placeholder="高层住宅/大型公建" />
            </Form.Item>
            <Form.Item name="annual_output" label="年产值（元）" style={{ width: '35%' }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="status" label="状态" style={{ width: '30%' }}>
              <Select options={STATUS_OPTIONS} />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="manager_id" label="项目经理">
            <Select allowClear showSearch optionFilterProp="label" options={users.map(u => ({ label: `${u.display_name} (${u.username})`, value: u.id }))} />
          </Form.Item>
          <Form.Item name="description" label="项目说明">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={drawerProject ? `${drawerProject.name}：成员与分区` : '成员与分区'}
        open={!!drawerProject}
        onClose={() => setDrawerProject(null)}
        width={760}
      >
        {drawerProject && (
          <Tabs
            items={[
              {
                key: 'members',
                label: '项目成员',
                children: (
                  <>
                    <Form form={memberForm} layout="inline" style={{ marginBottom: 16 }}>
                      <Form.Item name="user_id" rules={[{ required: true }]}>
                        <Select style={{ width: 220 }} placeholder="选择人员" showSearch optionFilterProp="label" options={users.map(u => ({ label: `${u.display_name} (${u.username})`, value: u.id }))} />
                      </Form.Item>
                      <Form.Item name="project_role" rules={[{ required: true }]}>
                        <Select style={{ width: 160 }} placeholder="项目角色" options={PROJECT_ROLE_OPTIONS} />
                      </Form.Item>
                      <Button type="primary" onClick={async () => {
                        const values = await memberForm.validateFields()
                        await addProjectMember(drawerProject.id, values)
                        memberForm.resetFields()
                        await loadDrawer(drawerProject)
                      }}>添加</Button>
                    </Form>
                    <Table
                      rowKey="id"
                      size="small"
                      pagination={false}
                      dataSource={members}
                      columns={[
                        { title: '姓名', dataIndex: 'display_name' },
                        { title: '账号', dataIndex: 'username' },
                        { title: '项目角色', dataIndex: 'project_role', render: v => PROJECT_ROLE_OPTIONS.find(o => o.value === v)?.label ?? v },
                        { title: '状态', dataIndex: 'left_at', render: v => v ? <Tag>已退出</Tag> : <Tag color="green">在岗</Tag> },
                        { title: '操作', render: (_, row: any) => !row.left_at && <Button danger size="small" onClick={async () => { await removeProjectMember(drawerProject.id, row.id); await loadDrawer(drawerProject) }}>移出</Button> },
                      ]}
                    />
                  </>
                ),
              },
              {
                key: 'zones',
                label: '工作分区',
                children: (
                  <>
                    <Form form={zoneForm} layout="inline" style={{ marginBottom: 16 }}>
                      <Form.Item name="name" rules={[{ required: true }]}>
                        <Input placeholder="分区名称" />
                      </Form.Item>
                      <Form.Item name="zone_code">
                        <Input placeholder="分区编码" />
                      </Form.Item>
                      <Button type="primary" onClick={async () => {
                        const values = await zoneForm.validateFields()
                        await createWorkZone(drawerProject.id, values)
                        zoneForm.resetFields()
                        await loadDrawer(drawerProject)
                      }}>添加</Button>
                    </Form>
                    <Table rowKey="id" size="small" pagination={false} dataSource={zones} columns={[
                      { title: '分区名称', dataIndex: 'name' },
                      { title: '分区编码', dataIndex: 'zone_code' },
                    ]} />
                  </>
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  )
}
