/**
 * 汇总统计条：展示 GET .../findings 响应 meta 里的 total/by_source/by_severity/by_status，
 * 作为「问题按闭环状态机操作」的进度概览，不做交互（点击筛选交给 FilterBar/Tab）。
 */
import { Space, Statistic, Tag } from 'antd'
import HelpTip from '@/components/HelpTip'
import type { FindingListMeta } from '@/services/findings'
import { SEVERITY_META, SOURCE_META, STATUS_META } from '@/services/findings'

interface StatsBarProps {
  meta: FindingListMeta | null
  loading: boolean
}

function countRow(
  counts: Record<string, number> | undefined,
  labelMap: Record<string, { label: string; color: string }>,
): JSX.Element[] {
  if (!counts) return []
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([key, n]) => {
      const meta = labelMap[key]
      return (
        <Tag key={key} color={meta?.color ?? 'default'}>
          {meta?.label ?? key} {n}
        </Tag>
      )
    })
}

export default function StatsBar({ meta, loading }: StatsBarProps): JSX.Element {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 24,
        alignItems: 'center',
        padding: '12px 16px',
        background: '#fafafa',
        borderRadius: 8,
        marginBottom: 16,
      }}
    >
      <Statistic
        title={
          <Space size={4}>
            问题总数
            <HelpTip content="项目下五类来源（单图/跨图/会审/语义/符号）问题的合计数，随筛选条件实时变化。" />
          </Space>
        }
        value={loading ? undefined : meta?.total ?? 0}
        loading={loading}
      />
      <div>
        <div style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)', marginBottom: 4 }}>按来源</div>
        <Space wrap size={4}>{countRow(meta?.by_source, SOURCE_META)}</Space>
      </div>
      <div>
        <div style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)', marginBottom: 4 }}>按严重度</div>
        <Space wrap size={4}>{countRow(meta?.by_severity, SEVERITY_META)}</Space>
      </div>
      <div>
        <div style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)', marginBottom: 4 }}>按状态</div>
        <Space wrap size={4}>{countRow(meta?.by_status, STATUS_META)}</Space>
      </div>
    </div>
  )
}
