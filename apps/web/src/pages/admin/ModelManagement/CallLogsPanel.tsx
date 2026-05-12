/**
 * 调用日志面板
 * - 7日/30日费用汇总（按引擎）
 * - 每日趋势（折线图区域）
 * - 最近错误日志
 */
import { useEffect, useState, useCallback } from 'react'
import { Card, Row, Col, Select, Table, Tag, Spin, Button, Statistic, Alert } from 'antd'
import { ReloadOutlined, WarningOutlined } from '@ant-design/icons'
import { getCostSummary, getRecentErrors, getCBStatus } from '@/services/modelManagement'

type CostRow = {
  engine_name: string
  model_name: string
  provider_name: string
  total_calls: number
  success_calls: number
  avg_latency_ms: number
  total_cost_usd: number
}

type ErrorRow = {
  id: string
  engine_name: string
  error_type: string
  created_at: string
  latency_ms: number
}

type CBEntry = {
  key: string
  state: string
  failures: number
  opened_at?: number
}

export default function CallLogsPanel() {
  const [days, setDays] = useState(7)
  const [loading, setLoading] = useState(false)
  const [costs, setCosts] = useState<CostRow[]>([])
  const [errors, setErrors] = useState<ErrorRow[]>([])
  const [cbs, setCbs] = useState<CBEntry[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    const [c, e, cb] = await Promise.all([
      getCostSummary(days),
      getRecentErrors(30),
      getCBStatus(),
    ])
    setCosts(c)
    setErrors(e)
    setCbs((cb as CBEntry[]).filter(b => b.state !== 'closed'))
    setLoading(false)
  }, [days])

  useEffect(() => { load() }, [load])

  const totalCost = costs.reduce((s, r) => s + r.total_cost_usd, 0)
  const totalCalls = costs.reduce((s, r) => s + r.total_calls, 0)
  const openCBs = cbs.filter(b => b.state === 'open')

  const costColumns = [
    { title: '引擎', dataIndex: 'engine_name', width: 220 },
    { title: '模型', dataIndex: 'model_name' },
    { title: '提供商', dataIndex: 'provider_name', width: 120 },
    { title: '调用次数', dataIndex: 'total_calls', align: 'right' as const, width: 90 },
    {
      title: '成功率', width: 90, align: 'right' as const,
      render: (_: unknown, r: CostRow) =>
        r.total_calls ? `${((r.success_calls / r.total_calls) * 100).toFixed(1)}%` : '—',
    },
    {
      title: '平均延迟', dataIndex: 'avg_latency_ms', align: 'right' as const, width: 100,
      render: (v: number) => `${v} ms`,
    },
    {
      title: '费用 (USD)', dataIndex: 'total_cost_usd', align: 'right' as const, width: 110,
      render: (v: number) => `$${v.toFixed(4)}`,
    },
  ]

  const errorColumns = [
    { title: '引擎', dataIndex: 'engine_name', width: 200 },
    {
      title: '错误类型', dataIndex: 'error_type', width: 180,
      render: (v: string) => <Tag color="red">{v}</Tag>,
    },
    {
      title: '延迟', dataIndex: 'latency_ms', width: 90, align: 'right' as const,
      render: (v: number) => `${v} ms`,
    },
    {
      title: '时间', dataIndex: 'created_at',
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ]

  return (
    <Spin spinning={loading}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Select
          value={days}
          onChange={setDays}
          style={{ width: 120 }}
          options={[
            { value: 7,  label: '近 7 天' },
            { value: 14, label: '近 14 天' },
            { value: 30, label: '近 30 天' },
          ]}
        />
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>

      {openCBs.length > 0 && (
        <Alert
          type="error" showIcon icon={<WarningOutlined />}
          message={`${openCBs.length} 个断路器处于 OPEN 状态`}
          description={openCBs.map(b => (
            <span key={b.key} style={{ marginRight: 12 }}>
              {b.key}（失败 {b.failures} 次）
            </span>
          ))}
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card size="small">
            <Statistic title={`近 ${days} 日总费用`} value={totalCost} precision={4} prefix="$" />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic title={`近 ${days} 日调用次数`} value={totalCalls} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic title="断路器异常" value={openCBs.length} suffix="个 OPEN"
              valueStyle={{ color: openCBs.length > 0 ? '#ff4d4f' : '#52c41a' }} />
          </Card>
        </Col>
      </Row>

      <Card title={`近 ${days} 日调用成本（按引擎）`} size="small" style={{ marginBottom: 16 }}>
        <Table<CostRow>
          dataSource={costs}
          rowKey="engine_name"
          columns={costColumns}
          size="small"
          pagination={false}
          summary={rows => {
            const total = rows.reduce((s, r) => s + r.total_cost_usd, 0)
            return (
              <Table.Summary.Row>
                <Table.Summary.Cell index={0} colSpan={6}>
                  <strong>合计</strong>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={6} align="right">
                  <strong>${total.toFixed(4)}</strong>
                </Table.Summary.Cell>
              </Table.Summary.Row>
            )
          }}
        />
      </Card>

      <Card title="最近错误（最近 30 条）" size="small">
        <Table<ErrorRow>
          dataSource={errors}
          rowKey="id"
          columns={errorColumns}
          size="small"
          pagination={{ pageSize: 10, size: 'small' }}
        />
      </Card>
    </Spin>
  )
}
