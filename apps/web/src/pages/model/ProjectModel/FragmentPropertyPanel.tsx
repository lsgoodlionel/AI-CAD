/**
 * Fragments 构件属性面板（A-08，WS2）。
 *
 * 展示拾取到的构件的 IFC 类型 / GUID / 名称 / localId 与 Pset 属性。
 * 与 `SemanticTreePanel` 的双向联动通过父级共享选中状态实现（不改 SemanticTreePanel 内部）：
 * - 三维拾取 → onSelect 上抛 item，父级用 findSemanticNodeForItem 定位树节点；
 * - 语义树选中 → 父级调用 FragmentsScene 外部高亮方法（并行 agent seam）。
 * 面板本身仅负责渲染 + 通过 onLocateInTree / onClear 回传意图。
 *
 * 展示逻辑抽为纯函数（toPrimaryEntries / toPsetSections）便于单测（无需 DOM）。
 */
import {
  ApartmentOutlined,
  AimOutlined,
  ClearOutlined,
} from '@ant-design/icons'
import {
  Button,
  Collapse,
  Descriptions,
  Empty,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import type { PickedFragmentItem } from '@/services/projectModel'

const { Text } = Typography

export interface PropertyEntry {
  label: string
  value: string
}

export interface PsetSection {
  name: string
  entries: PropertyEntry[]
}

/** 归一化任意 Pset 值为可显示字符串（纯函数） */
export function formatPropertyValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value.trim() === '' ? '—' : value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/** 构件基础属性 → 展示行（纯函数，供渲染与单测复用） */
export function toPrimaryEntries(item: PickedFragmentItem): PropertyEntry[] {
  const entries: PropertyEntry[] = [
    { label: 'IFC 类型', value: item.ifcType || '未知' },
    { label: 'GUID', value: item.guid ?? '—' },
    { label: '名称', value: item.name ?? '—' },
    {
      label: 'localId',
      value: item.localId === null || item.localId === undefined ? '—' : String(item.localId),
    },
  ]
  return entries
}

/** Pset → 分组展示（纯函数） */
export function toPsetSections(item: PickedFragmentItem): PsetSection[] {
  if (!item.psets) return []
  return Object.entries(item.psets).map(([name, props]) => ({
    name,
    entries: Object.entries(props).map(([label, value]) => ({
      label,
      value: formatPropertyValue(value),
    })),
  }))
}

export interface FragmentPropertyPanelProps {
  item: PickedFragmentItem | null
  loading?: boolean
  /** 清除当前选择 */
  onClear?: () => void
  /** 在语义树中定位（父级据此驱动 SemanticTreePanel 选中） */
  onLocateInTree?: (item: PickedFragmentItem) => void
}

export default function FragmentPropertyPanel({
  item,
  loading = false,
  onClear,
  onLocateInTree,
}: FragmentPropertyPanelProps) {
  if (loading) {
    return (
      <div data-testid="fragment-property-loading" style={{ padding: 24, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  if (!item) {
    return (
      <div data-testid="fragment-property-empty">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="点击三维构件查看 IFC 属性"
        />
      </div>
    )
  }

  const primary = toPrimaryEntries(item)
  const psetSections = toPsetSections(item)

  return (
    <div data-testid="fragment-property-panel">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space size={6}>
            <ApartmentOutlined />
            <Text strong data-testid="fragment-property-title">
              {item.name ?? item.ifcType ?? '构件'}
            </Text>
            {item.ifcType ? <Tag color="blue">{item.ifcType}</Tag> : null}
          </Space>
          <Space size={4}>
            <Button
              size="small"
              type="text"
              icon={<AimOutlined />}
              disabled={!onLocateInTree}
              onClick={() => onLocateInTree?.(item)}
            >
              语义树定位
            </Button>
            <Button
              size="small"
              type="text"
              icon={<ClearOutlined />}
              disabled={!onClear}
              onClick={() => onClear?.()}
            />
          </Space>
        </Space>

        <Descriptions
          size="small"
          column={1}
          bordered
          data-testid="fragment-property-primary"
          items={primary.map((entry) => ({
            key: entry.label,
            label: entry.label,
            children: entry.value,
          }))}
        />

        {psetSections.length > 0 ? (
          <Collapse
            size="small"
            data-testid="fragment-property-psets"
            defaultActiveKey={psetSections.map((section) => section.name)}
            items={psetSections.map((section) => ({
              key: section.name,
              label: section.name,
              children: (
                <Descriptions
                  size="small"
                  column={1}
                  items={section.entries.map((entry) => ({
                    key: entry.label,
                    label: entry.label,
                    children: entry.value,
                  }))}
                />
              ),
            }))}
          />
        ) : (
          <Text type="secondary">无 Pset 属性</Text>
        )}
      </Space>
    </div>
  )
}
