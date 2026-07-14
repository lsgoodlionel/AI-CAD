/**
 * 审查中心筛选条：来源 Tab + 严重度/状态/图纸/套图批次筛选 + 刷新。
 *
 * 「套图批次」筛选取舍（见交接说明）：Finding 统一模型里只有 cross（跨图）来源的
 * source_key 携带批次前缀（"{batch_id}:cluster:xxx"），其余四类来源没有批次维度。
 * 因此批次筛选仅在「跨图」Tab 下生效，其它 Tab 选择批次不产生效果，用 disabled +
 * HelpTip 提示，避免用户误以为筛选覆盖全部来源。
 */
import { Button, Select, Space, Tabs } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import HelpTip from '@/components/HelpTip'
import type { FindingSeverity, FindingStatus } from '@/services/findings'
import { SEVERITY_META, STATUS_META } from '@/services/findings'
import { REVIEW_TABS } from './constants'
import type { BatchOption, DrawingOption, ReviewTabDef } from './constants'

interface FilterBarProps {
  activeTab: ReviewTabDef['key']
  onTabChange: (key: ReviewTabDef['key']) => void
  severity: FindingSeverity | undefined
  onSeverityChange: (value: FindingSeverity | undefined) => void
  status: FindingStatus | undefined
  onStatusChange: (value: FindingStatus | undefined) => void
  drawingId: string | undefined
  onDrawingChange: (value: string | undefined) => void
  drawingOptions: DrawingOption[]
  batchId: string | undefined
  onBatchChange: (value: string | undefined) => void
  batchOptions: BatchOption[]
  loading: boolean
  onRefresh: () => void
}

export default function FilterBar({
  activeTab,
  onTabChange,
  severity,
  onSeverityChange,
  status,
  onStatusChange,
  drawingId,
  onDrawingChange,
  drawingOptions,
  batchId,
  onBatchChange,
  batchOptions,
  loading,
  onRefresh,
}: FilterBarProps): JSX.Element {
  const batchFilterUsable = activeTab === 'cross'

  return (
    <div style={{ marginBottom: 8 }}>
      <Tabs
        activeKey={activeTab}
        onChange={(key) => onTabChange(key as ReviewTabDef['key'])}
        items={REVIEW_TABS.map((t) => ({ key: t.key, label: t.label }))}
      />
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder="严重度"
          style={{ width: 120 }}
          value={severity}
          onChange={onSeverityChange}
          options={Object.entries(SEVERITY_META).map(([value, m]) => ({
            value,
            label: m.label,
          }))}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 130 }}
          value={status}
          onChange={onStatusChange}
          options={Object.entries(STATUS_META).map(([value, m]) => ({
            value,
            label: m.label,
          }))}
        />
        <Select
          allowClear
          showSearch
          placeholder="按图纸筛选"
          style={{ width: 220 }}
          value={drawingId}
          onChange={onDrawingChange}
          optionFilterProp="label"
          options={drawingOptions.map((d) => ({ value: d.id, label: d.label }))}
        />
        <Space size={4}>
          <Select
            allowClear
            disabled={!batchFilterUsable}
            placeholder="套图批次（仅跨图 Tab 生效）"
            style={{ width: 220 }}
            value={batchFilterUsable ? batchId : undefined}
            onChange={onBatchChange}
            options={batchOptions.map((b) => ({ value: b.id, label: b.label }))}
          />
          <HelpTip content="套图批次是跨图问题的分组维度；单图/会审/语义/符号问题不属于任何批次，切到「跨图」Tab 后此筛选才生效。" />
        </Space>
        <Button icon={<ReloadOutlined />} onClick={onRefresh} loading={loading}>
          刷新
        </Button>
      </Space>
    </div>
  )
}
