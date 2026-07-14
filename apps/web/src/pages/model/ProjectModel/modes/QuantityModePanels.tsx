/**
 * 算量模式右栏面板（D-13）：QTO 汇总（混凝土/模板/钢筋，分楼层/分单体下钻）
 * + 构件高亮（点击类别在 3D 视图中隔离查看）+「打开算量中心」直达详情/导出。
 *
 * 汇总数据只读复用 services/quantities.ts（D-12 已建，口径与 model_qto_summary.py
 * 一字不差），本页不重复实现算量逻辑，只做「模型页内快速查看 + 高亮联动」。
 */
import { useEffect, useState } from 'react'
import { history } from '@umijs/max'
import { Alert, Button, Card, Checkbox, Empty, Space, Spin, Table, Tag, Typography } from 'antd'
import { ExportOutlined } from '@ant-design/icons'
import type { ModelScene } from '@/services/projectModel'
import { getProjectQuantities } from '@/services/quantities'
import type { ProjectQtoData, ProjectQtoEnvelope, QtoByTypeBucket } from '@/services/quantities'
import { elementFilterOptions } from './elementFilterOptions'

const { Text, Title } = Typography

interface QuantityModePanelsProps {
  projectId: string
  isV2: boolean
  viewScene: ModelScene | null
  elementFilter: string[] | undefined
  onElementFilterChange: (values: string[]) => void
}

interface ByTypeRow extends QtoByTypeBucket {
  key: string
}

function toRows(byType: Record<string, QtoByTypeBucket>): ByTypeRow[] {
  return Object.entries(byType).map(([key, bucket]) => ({ key, ...bucket }))
}

export default function QuantityModePanels({
  projectId,
  isV2,
  viewScene,
  elementFilter,
  onElementFilterChange,
}: QuantityModePanelsProps) {
  const [data, setData] = useState<ProjectQtoData | null>(null)
  const [loading, setLoading] = useState(true)
  const [notBuilt, setNotBuilt] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setNotBuilt(false)
    getProjectQuantities(projectId)
      .then((res: ProjectQtoEnvelope) => { if (alive) setData(res.success ? res.data : null) })
      .catch((error: unknown) => {
        if (!alive) return
        const status = (error as { response?: { status?: number } })?.response?.status
        if (status === 404) setNotBuilt(true)
        setData(null)
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [projectId])

  const rows = data ? toRows(data.project.by_type) : []

  return (
    <>
      <Card
        size="small"
        title="QTO 汇总"
        style={{ marginBottom: 12 }}
        extra={(
          <Button
            size="small"
            icon={<ExportOutlined />}
            onClick={() => history.push(`/projects/${projectId}/quantities`)}
          >
            打开算量中心
          </Button>
        )}
      >
        {loading ? (
          <Spin size="small" />
        ) : notBuilt ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="模型尚未构建，暂无算量数据" />
        ) : !data ? (
          <Alert type="warning" showIcon message="算量数据加载失败，请稍后重试" />
        ) : (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
              <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: '8px 10px', background: '#fafafa' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>混凝土净体积</Text>
                <div style={{ fontWeight: 600, fontSize: 16 }}>{data.project.concrete.net_m3.toFixed(1)} m³</div>
              </div>
              <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: '8px 10px', background: '#fafafa' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>模板接触面积</Text>
                <div style={{ fontWeight: 600, fontSize: 16 }}>{data.project.formwork.contact_m2.toFixed(1)} m²</div>
              </div>
              {data.project.rebar && !data.project.rebar.missing ? (
                <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: '8px 10px', background: '#fafafa' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>钢筋量</Text>
                  <div style={{ fontWeight: 600, fontSize: 16 }}>{(data.project.rebar.total_t ?? 0).toFixed(2)} t</div>
                </div>
              ) : null}
              <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: '8px 10px', background: '#fafafa' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>覆盖率</Text>
                <div style={{ fontWeight: 600, fontSize: 16 }}>
                  {data.project.measured_count}/{data.project.element_count}
                  {data.project.uncovered_count > 0 ? (
                    <Tag color="orange" style={{ marginLeft: 6 }}>{data.project.uncovered_count} 未覆盖</Tag>
                  ) : null}
                </div>
              </div>
            </div>

            {rows.length > 0 ? (
              <Table
                size="small"
                pagination={false}
                dataSource={rows}
                rowKey="key"
                columns={[
                  { title: '类型', dataIndex: 'key', key: 'key' },
                  { title: '数量', dataIndex: 'count', key: 'count', width: 64 },
                  {
                    title: '净体积(m³)', dataIndex: 'net_m3', key: 'net_m3', width: 96,
                    render: (v: number) => v.toFixed(1),
                  },
                  {
                    title: '', key: 'action', width: 64,
                    render: (_: unknown, row: ByTypeRow) => (
                      <Button size="small" type="link" onClick={() => onElementFilterChange([row.key])}>
                        高亮
                      </Button>
                    ),
                  },
                ]}
              />
            ) : null}

            {data.by_floor.length > 0 ? (
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Title level={5} style={{ margin: 0, fontSize: 13 }}>分楼层</Title>
                {data.by_floor.map((floor) => (
                  <Space key={floor.floor_key} style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text style={{ fontSize: 12 }}>{floor.floor_label}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {floor.concrete.net_m3.toFixed(1)} m³ · {floor.formwork.contact_m2.toFixed(1)} m²
                    </Text>
                  </Space>
                ))}
              </Space>
            ) : null}
          </Space>
        )}
      </Card>

      {isV2 && viewScene ? (
        <Card size="small" title="构件高亮">
          <Checkbox.Group
            style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
            value={elementFilter ?? elementFilterOptions(viewScene).map((o) => o.value)}
            onChange={(values) => onElementFilterChange(values as string[])}
            options={elementFilterOptions(viewScene)}
          />
        </Card>
      ) : null}
    </>
  )
}
