/**
 * 浏览模式左栏面板（D-13）：结构导航（语义树/单体/楼层）+ 筛选（专业/严重度/标记类型）
 * + 构件图层 + 模型质量。从原 index.tsx 左栏 Card 列表迁出并按「≤4 常驻面板」合并整理。
 */
import type { ReactNode } from 'react'
import { Card, Checkbox, List, Space, Tabs, Tag, Typography } from 'antd'
import type { ModelScene, SceneFloor } from '@/services/projectModel'
import SemanticTreePanel from '../SemanticTreePanel'
import ModelQualityPanel from '../ModelQualityPanel'
import CollapsiblePanel from '../CollapsiblePanel'
import HelpTip from '@/components/HelpTip'
import type {
  BuildingUnitOption, ModelQualitySummary, SemanticScopeLodView,
  SemanticTreeGroup, SemanticTreeNodeView,
} from '../types'
import { DISCIPLINE_LABEL, SEVERITY_META } from '../modelWorkspaceConstants'
import { elementFilterOptions } from './elementFilterOptions'

const { Text } = Typography

interface BrowseModePanelsProps {
  semanticTreeGroups: SemanticTreeGroup[]
  selectedSemanticNode: SemanticTreeNodeView | null
  onSelectSemanticNode: (node: SemanticTreeNodeView | null) => void
  buildingUnits: BuildingUnitOption[]
  selectedBuildingKey: string | null
  onSelectBuilding: (key: string | null) => void
  sortedFloors: SceneFloor[]
  isolatedFloorKey: string | null
  onIsolateFloor: (key: string | null) => void
  availableDisciplines: string[]
  disciplineFilter: string[]
  onDisciplineFilterChange: (values: string[]) => void
  severityFilter: string[]
  onSeverityFilterChange: (values: string[]) => void
  markerTypeFilter: string[]
  onMarkerTypeFilterChange: (values: string[]) => void
  isV2: boolean
  viewScene: ModelScene | null
  elementFilter: string[] | undefined
  onElementFilterChange: (values: string[]) => void
  quality: ModelQualitySummary
  selectedScopeQuality: SemanticScopeLodView | null
}

const ALL_SEVERITIES = ['critical', 'major', 'minor', 'info']
const ALL_MARKER_TYPES = ['issue', 'cross']
const MARKER_TYPE_LABEL: Record<string, string> = { issue: '图内问题', cross: '跨图发现' }

export default function BrowseModePanels({
  semanticTreeGroups,
  selectedSemanticNode,
  onSelectSemanticNode,
  buildingUnits,
  selectedBuildingKey,
  onSelectBuilding,
  sortedFloors,
  isolatedFloorKey,
  onIsolateFloor,
  availableDisciplines,
  disciplineFilter,
  onDisciplineFilterChange,
  severityFilter,
  onSeverityFilterChange,
  markerTypeFilter,
  onMarkerTypeFilterChange,
  isV2,
  viewScene,
  elementFilter,
  onElementFilterChange,
  quality,
  selectedScopeQuality,
}: BrowseModePanelsProps) {
  const navTabs = [
    semanticTreeGroups.length > 0 ? {
      key: 'semantic',
      label: '语义树',
      children: (
        <SemanticTreePanel
          groups={semanticTreeGroups}
          selectedNodeId={selectedSemanticNode?.id}
          onSelectNode={onSelectSemanticNode}
        />
      ),
    } : null,
    buildingUnits.length > 0 ? {
      key: 'units',
      label: '单体',
      children: (
        <List
          size="small"
          dataSource={buildingUnits}
          renderItem={(building) => {
            const isActive = selectedBuildingKey === building.key
            return (
              <List.Item
                onClick={() => onSelectBuilding(isActive ? null : building.key)}
                style={{
                  cursor: 'pointer', paddingLeft: 8, paddingRight: 8,
                  background: isActive ? '#e6f4ff' : undefined, borderRadius: 6,
                }}
              >
                <Space wrap>
                  <Text strong={isActive}>{building.label}</Text>
                  <Tag>{building.source === 'manual' ? '人工' : '识别'}</Tag>
                  {!building.hasGeometry ? <Tag color="default">无几何</Tag> : null}
                </Space>
              </List.Item>
            )
          }}
        />
      ),
    } : null,
    {
      key: 'floors',
      label: '楼层',
      children: (
        <List
          size="small"
          dataSource={sortedFloors}
          renderItem={(floor) => {
            const isActive = isolatedFloorKey === floor.key
            return (
              <List.Item
                onClick={() => onIsolateFloor(isActive ? null : floor.key)}
                style={{
                  cursor: 'pointer', paddingLeft: 8, paddingRight: 8,
                  background: isActive ? '#e6f4ff' : undefined, borderRadius: 6,
                }}
              >
                <Space>
                  <Text strong={isActive}>{floor.label}</Text>
                  <Text type="secondary">{floor.drawings.length} 张</Text>
                </Space>
              </List.Item>
            )
          }}
        />
      ),
    },
  ].filter(Boolean) as { key: string; label: string; children: ReactNode }[]

  return (
    <>
      <Card
        size="small"
        title={<>结构导航<HelpTip content="按语义树/单体/楼层三种维度浏览模型结构，点击可在 3D 视图中隔离/定位。" anchor="9-左栏结构导航" /></>}
        style={{ marginBottom: 12 }}
        styles={{ body: { padding: '8px 12px' } }}
      >
        <Tabs size="small" items={navTabs} tabBarStyle={{ marginBottom: 8 }} />
      </Card>

      <Card
        size="small"
        title="筛选"
        style={{ marginBottom: 12 }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>专业</Text>
            <Checkbox.Group
              style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}
              value={disciplineFilter}
              onChange={(values) => onDisciplineFilterChange(values as string[])}
              options={availableDisciplines.map((discipline) => ({
                label: DISCIPLINE_LABEL[discipline] ?? discipline,
                value: discipline,
              }))}
            />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>严重度</Text>
            <Checkbox.Group
              style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}
              value={severityFilter}
              onChange={(values) => onSeverityFilterChange(values as string[])}
              options={ALL_SEVERITIES.map((severity) => ({
                label: SEVERITY_META[severity].label,
                value: severity,
              }))}
            />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>标记类型</Text>
            <Checkbox.Group
              style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}
              value={markerTypeFilter}
              onChange={(values) => onMarkerTypeFilterChange(values as string[])}
              options={ALL_MARKER_TYPES.map((type) => ({ label: MARKER_TYPE_LABEL[type], value: type }))}
            />
          </div>
        </Space>
      </Card>

      {isV2 && viewScene ? (
        <Card size="small" title="构件图层" style={{ marginBottom: 12 }}>
          <Checkbox.Group
            style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
            value={elementFilter ?? elementFilterOptions(viewScene).map((o) => o.value)}
            onChange={(values) => onElementFilterChange(values as string[])}
            options={elementFilterOptions(viewScene)}
          />
        </Card>
      ) : null}

      <CollapsiblePanel
        title={<>模型质量<HelpTip content="汇总楼层未分配、楼层冲突、低置信构件、待人工确认等模型健康指标，用于判断当前模型是否可放心用于审图/算量。" anchor="12-1-模型质量" /></>}
        defaultOpen={false}
        maxBodyHeight={420}
      >
        <ModelQualityPanel quality={quality} buildingUnits={buildingUnits} selectedScopeQuality={selectedScopeQuality} />
      </CollapsiblePanel>
    </>
  )
}
