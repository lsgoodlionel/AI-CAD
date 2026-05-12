/**
 * 健康状态看板：提供商连通性 + 断路器状态 + 实时成本摘要
 */
import { useEffect, useState } from 'react'
import { Card, Row, Col, Badge, Statistic, Table, Alert, Spin, Button } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { checkAllHealth, getCostSummary, getCBStatus } from '@/services/modelManagement'

type ProviderHealth = { name: string; healthy: boolean }
type CBEntry = { key: string; state: string; failures: number; opened_at?: number }
type CostRow = {
  engine_name: string; model_name: string; provider_name: string
  total_calls: number; success_calls: number
  avg_latency_ms: number; total_cost_usd: number
}

export default function HealthDashboard() {
  const [loading, setLoading] = useState(true)
  const [health, setHealth] = useState<Record<string, boolean>>({})
  const [cbs, setCbs] = useState<CBEntry[]>([])
  const [costs, setCosts] = useState<CostRow[]>([])

  const refresh = async () => {
    setLoading(true)
    const [h, c, cb] = await Promise.all([
      checkAllHealth(),
      getCostSummary(7),
      getCBStatus(),
    ])
    setHealth(h)
    setCosts(c)
    setCbs(cb.filter((e: CBEntry) => e.state !== 'closed'))
    setLoading(false)
  }

  useEffect(() => { refresh() }, [])

  const openCBs = cbs.filter(c => c.state === 'open')

  return (
    <Spin spinning={loading}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
      </div>

      {openCBs.length > 0 && (
        <Alert
          type="error" showIcon
          message={`${openCBs.length} 个断路器处于 OPEN 状态`}
          description={openCBs.map(c => c.key).join('、')}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 提供商健康状态 */}
      <Row gutter={12} style={{ marginBottom: 16 }}>
        {Object.entries(health).map(([name, ok]) => (
          <Col key={name}>
            <Card size="small" style={{ width: 160 }}>
              <Badge status={ok ? 'success' : 'error'} text={name} />
              <div style={{ color: ok ? '#52c41a' : '#ff4d4f', fontSize: 12, marginTop: 4 }}>
                {ok ? 'ONLINE' : 'OFFLINE'}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 7日成本汇总 */}
      <Card title="近 7 日调用成本（按引擎）" size="small" style={{ marginBottom: 16 }}>
        <Table<CostRow>
          dataSource={costs} rowKey="engine_name" size="small" pagination={false}
          columns={[
            { title: '引擎', dataIndex: 'engine_name', width: 200 },
            { title: '模型', dataIndex: 'model_name' },
            { title: '提供商', dataIndex: 'provider_name', width: 100 },
            { title: '调用次数', dataIndex: 'total_calls', align: 'right', width: 90 },
            {
              title: '成功率', width: 90, align: 'right',
              render: (_, r) => `${((r.success_calls / r.total_calls) * 100).toFixed(1)}%`,
            },
            {
              title: '平均延迟', dataIndex: 'avg_latency_ms', align: 'right', width: 100,
              render: v => `${v} ms`,
            },
            {
              title: '费用(USD)', dataIndex: 'total_cost_usd', align: 'right', width: 100,
              render: v => `$${v.toFixed(4)}`,
            },
          ]}
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

      {/* 断路器状态（非 closed） */}
      {cbs.length > 0 && (
        <Card title="断路器异常状态" size="small">
          <Table
            dataSource={cbs} rowKey="key" size="small" pagination={false}
            columns={[
              { title: '引擎/任务类型', dataIndex: 'key', width: 240 },
              {
                title: '状态', dataIndex: 'state', width: 100,
                render: v => (
                  <Badge
                    status={v === 'open' ? 'error' : 'warning'}
                    text={v.toUpperCase()}
                  />
                ),
              },
              { title: '失败次数', dataIndex: 'failures', width: 90 },
              {
                title: '断开时间', dataIndex: 'opened_at', width: 160,
                render: v => v ? new Date(v * 1000).toLocaleString('zh-CN') : '-',
              },
            ]}
          />
        </Card>
      )}
    </Spin>
  )
}
