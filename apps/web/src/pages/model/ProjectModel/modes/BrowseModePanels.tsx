/**
 * 浏览模式左栏面板（D-13）：结构导航（语义树/单体/楼层）+ 筛选（专业/严重度/标记类型）
 * + 构件图层 + 模型质量。从原 index.tsx 左栏 Card 列表迁出并按「≤4 常驻面板」合并整理。
 */
import type { ReactNode } from 'react'
import { Card, Checkbox, List, Space, Tabs, Tag, Tooltip, Typography } from 'antd'
import type { ModelScene, SceneFloor } from '@/services/projectModel'
import SemanticTreePanel from '../SemanticTreePanel'
import ModelQualityPanel from '../ModelQualityPanel'
import ComponentSummaryCard from '../ComponentSummaryCard'
import CollapsiblePanel from '../CollapsiblePanel'
import SetCapabilityPanel from '../SetCapabilityPanel'
import HelpTip from '@/components/HelpTip'
import type {
  BuildingUnitOption, ModelQualitySummary, SemanticScopeLodView,
  SemanticTreeGroup, SemanticTreeNodeView,
} from '../types'
import { DISCIPLINE_LABEL, SEVERITY_META } from '../modelWorkspaceConstants'
import { elementFilterOptions } from './elementFilterOptions'

const { Text } = Typography

interface BrowseModePanelsProps {
  projectId: string
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
  projectId,
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
                <Space wrap>
                  <Text strong={isActive}>{floor.label}</Text>
                  <Text type="secondary">{floor.drawings.length} 张</Text>
                  {/*
                    楼层级门禁:标高是图纸读的还是默认值推的。
                    实测 v31 有 10/13 层是 4.5m 默认值硬推,最大偏差 11.9m,
                    而界面上与图纸值长得一模一样 —— 必须在这里区分开。
                  */}
                  {(() => {
                    const meta = floor as {
                      elevation_estimated?: boolean
                      elevation_source?: string
                      elevation_sources?: string[]
                    }
                    if (meta.elevation_estimated) {
                      return (
                        <Tooltip title="标高由默认层高推出（或累加链上用过默认层高），不是图纸实测值">
                          <Tag color="orange">标高为默认值</Tag>
                        </Tooltip>
                      )
                    }
                    if (!meta.elevation_source) return null
                    // 一层可由多个单体贡献，来源可能不同（如 north 读自图纸配对、
                    // main 是人工录入）。此时后端报 `mixed` + 明细，
                    // 只显示其中一个会让人以为整层都是那个来源。
                    const sources = meta.elevation_sources ?? [meta.elevation_source]
                    return (
                      <Tooltip title={`标高来源：${sources.join('、')}`}>
                        <Tag color="green">
                          {meta.elevation_source === 'mixed'
                            ? `标高来自图纸（${sources.length} 种来源）`
                            : '标高来自图纸'}
                        </Tag>
                      </Tooltip>
                    )
                  })()}
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

      {/*
        建模能力与降级说明。**默认展开、排在模型质量之前**——
        降级如果被折叠起来，用户就会把默认层高当成图纸实测值
        （实测：13 层里 10 层是 4.5m 默认值推的，界面上看不出来）。
      */}
      <CollapsiblePanel
        title={<>建模能力与降级<HelpTip content="这批图纸能建到什么程度：有无坐标基准图（决定世界坐标）、有无完整平面图（决定楼层）、有无立面/剖面图（决定层高是实测还是默认值）。降级项会逐条列出。" anchor="12-0-建模能力" /></>}
        defaultOpen
        maxBodyHeight={460}
      >
        <SetCapabilityPanel payload={viewScene?.set_capability} />
      </CollapsiblePanel>

      <CollapsiblePanel
        title={<>模型质量<HelpTip content="汇总楼层未分配、楼层冲突、低置信构件、待人工确认等模型健康指标，用于判断当前模型是否可放心用于审图/算量。" anchor="12-1-模型质量" /></>}
        defaultOpen={false}
        maxBodyHeight={420}
      >
        <ComponentSummaryCard projectId={projectId} />
        <ModelQualityPanel quality={quality} buildingUnits={buildingUnits} selectedScopeQuality={selectedScopeQuality} />
      </CollapsiblePanel>
    </>
  )
}
