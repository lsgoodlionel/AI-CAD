import { useRef, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { ProTable } from '@ant-design/pro-components'
import type { ActionType, ProColumns } from '@ant-design/pro-components'
import { Button, Badge, Tag } from 'antd'
import { PlusOutlined, EyeOutlined } from '@ant-design/icons'
import { listProposals } from '@/services/incentive'
import SubmitProposalModal from '../SubmitProposalModal'

const STATUS_MAP: Record<string, { status: 'default' | 'processing' | 'success' | 'error' | 'warning'; text: string }> = {
  draft:         { status: 'default',    text: '草稿' },
  calculating:   { status: 'processing', text: '核算中' },
  pending_sign:  { status: 'warning',    text: '待签字' },
  public_notice: { status: 'processing', text: '公示期' },
  distributing:  { status: 'processing', text: '分配中' },
  approved:      { status: 'success',    text: '已审批' },
  paid:          { status: 'success',    text: '已兑现' },
  rejected:      { status: 'error',      text: '已驳回' },
}

const TYPE_TAG: Record<string, { color: string; label: string }> = {
  A: { color: 'blue',  label: 'A类·设计变更' },
  B: { color: 'green', label: 'B类·施工优化' },
}

interface ProposalRow {
  id: string
  proposal_type: 'A' | 'B'
  title: string
  status: string
  raw_saving_est: number | null
  net_saving: number | null
  proposer_name: string
  project_name: string
  created_at: string
}

export default function ProposalList() {
  const actionRef = useRef<ActionType>()
  const navigate = useNavigate()
  const [submitOpen, setSubmitOpen] = useState(false)

  const columns: ProColumns<ProposalRow>[] = [
    {
      title: '提案类型',
      dataIndex: 'proposal_type',
      width: 120,
      render: (_, row) => {
        const t = TYPE_TAG[row.proposal_type]
        return t ? <Tag color={t.color}>{t.label}</Tag> : row.proposal_type
      },
      valueEnum: { A: { text: 'A类' }, B: { text: 'B类' } },
    },
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (_, row) => {
        const s = STATUS_MAP[row.status] ?? { status: 'default', text: row.status }
        return <Badge status={s.status} text={s.text} />
      },
      valueEnum: Object.fromEntries(
        Object.entries(STATUS_MAP).map(([k, v]) => [k, { text: v.text }])
      ),
    },
    {
      title: '预估节约',
      dataIndex: 'raw_saving_est',
      search: false,
      width: 110,
      render: (_, row) =>
        row.raw_saving_est ? `¥${(row.raw_saving_est / 10000).toFixed(1)}万` : '—',
    },
    {
      title: '核算净节约',
      dataIndex: 'net_saving',
      search: false,
      width: 120,
      render: (_, row) =>
        row.net_saving !== null && row.net_saving !== undefined
          ? <span style={{ fontWeight: 600, color: '#389e0d' }}>¥{(row.net_saving / 10000).toFixed(1)}万</span>
          : '—',
    },
    {
      title: '提案人',
      dataIndex: 'proposer_name',
      search: false,
      width: 90,
    },
    {
      title: '所属项目',
      dataIndex: 'project_name',
      search: false,
      ellipsis: true,
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      search: false,
      width: 150,
      render: (_, row) => new Date(row.created_at).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 80,
      search: false,
      render: (_, row) => (
        <Button
          type="link"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/incentive/${row.id}`)}
        >
          查看
        </Button>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <ProTable<ProposalRow>
        actionRef={actionRef}
        rowKey="id"
        headerTitle="创效激励提案"
        columns={columns}
        request={async (params) => {
          const { current, pageSize, proposal_type, status } = params
          const res = await listProposals({
            proposal_type,
            status,
            limit: pageSize,
            offset: ((current ?? 1) - 1) * (pageSize ?? 20),
          })
          return { data: res.items, total: res.total, success: true }
        }}
        pagination={{ pageSize: 20 }}
        toolBarRender={() => [
          <Button
            key="submit"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setSubmitOpen(true)}
          >
            提交提案
          </Button>,
        ]}
      />

      <SubmitProposalModal
        open={submitOpen}
        onClose={() => setSubmitOpen(false)}
        onSuccess={() => actionRef.current?.reload()}
      />
    </div>
  )
}
