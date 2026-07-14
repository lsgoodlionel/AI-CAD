/**
 * 算量中心下钻表：分楼层 / 分单体，同一汇总口径（QtoSummary）逐行展示。
 */
import { Tabs, Table, Tag } from 'antd'
import type { QtoBuildingSummary, QtoFloorSummary } from '@/services/quantities'

interface FloorBreakdownTableProps {
  byFloor: QtoFloorSummary[]
  byBuilding: QtoBuildingSummary[]
}

const BASE_COLUMNS = [
  { title: '混凝土净体积(m³)', dataIndex: ['concrete', 'net_m3'], key: 'net_m3', width: 150 },
  { title: '混凝土毛体积(m³)', dataIndex: ['concrete', 'gross_m3'], key: 'gross_m3', width: 150 },
  { title: '模板接触面积(m²)', dataIndex: ['formwork', 'contact_m2'], key: 'contact_m2', width: 150 },
  { title: '构件总数', dataIndex: 'element_count', key: 'element_count', width: 100 },
  {
    title: '估算占比',
    key: 'estimated_ratio',
    width: 110,
    render: (_: unknown, row: QtoFloorSummary | QtoBuildingSummary) =>
      row.element_count > 0
        ? `${((row.estimated_count / row.element_count) * 100).toFixed(1)}%`
        : '—',
  },
  {
    title: '未覆盖',
    dataIndex: 'uncovered_count',
    key: 'uncovered_count',
    width: 90,
    render: (v: number) => (v > 0 ? <Tag color="warning">{v}</Tag> : v),
  },
]

export default function FloorBreakdownTable({ byFloor, byBuilding }: FloorBreakdownTableProps) {
  const floorColumns = [
    { title: '楼层', dataIndex: 'floor_label', key: 'floor_label', fixed: 'left' as const, width: 140 },
    ...BASE_COLUMNS,
  ]
  const buildingColumns = [
    { title: '单体', dataIndex: 'building_key', key: 'building_key', fixed: 'left' as const, width: 140 },
    ...BASE_COLUMNS,
  ]

  return (
    <Tabs
      style={{ marginTop: 16 }}
      items={[
        {
          key: 'by_floor',
          label: `分楼层（${byFloor.length}）`,
          children: (
            <Table
              size="small"
              rowKey="floor_key"
              scroll={{ x: 900 }}
              pagination={false}
              columns={floorColumns}
              dataSource={byFloor}
            />
          ),
        },
        {
          key: 'by_building',
          label: `分单体（${byBuilding.length}）`,
          children: (
            <Table
              size="small"
              rowKey="building_key"
              scroll={{ x: 900 }}
              pagination={false}
              columns={buildingColumns}
              dataSource={byBuilding}
            />
          ),
        },
      ]}
    />
  )
}
