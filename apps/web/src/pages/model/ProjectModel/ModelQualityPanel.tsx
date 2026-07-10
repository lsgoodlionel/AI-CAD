import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from '@umijs/max'
import {
  Alert,
  Divider,
  Empty,
  Progress,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ReviewMetrics } from '@/services/modelReview'
import { getReviewMetrics } from '@/services/modelReview'
import type {
  BuildingUnitOption,
  ModelQualitySummary,
  SemanticScopeLodView,
} from './types'

const { Text } = Typography

interface ModelQualityPanelProps {
  quality: ModelQualitySummary
  buildingUnits: BuildingUnitOption[]
  selectedScopeQuality?: SemanticScopeLodView | null
}

// ── C-17 返工收敛度量口径（与后端 dashboard.py 一致，避免古德哈特反噬）──
//   rework（返工点）= reclass 改类 + reject 否定 + addbox 补框；
//   confirm = 机器初模一次通过；收敛趋势下降印证「AI 出初模→人工审改」
//   效率提升落在 25–30% 现实区间（诚实边界，不承诺自动出图）。
const DISCIPLINE_OPTIONS = ['结构', '机电', '装修', '建筑'] as const

const CATEGORY_LABELS: Record<string, string> = {
  column: '柱',
  beam: '梁',
  slab: '板',
  wall: '墙',
  door: '门',
  window: '窗',
  pipe: '管线',
  equipment: '设备',
  axis: '轴网',
}

function unitLabel(buildingUnits: BuildingUnitOption[], key?: string) {
  if (!key) return '未知单体'
  return buildingUnits.find((unit) => unit.key === key)?.label ?? key
}

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

/** 返工率越高越差：红→黄→绿。 */
function reworkColor(rate: number): string {
  if (rate >= 0.5) return '#ff4d4f'
  if (rate >= 0.25) return '#faad14'
  return '#52c41a'
}

function shortPeriod(period: string): string {
  return period.length >= 10 ? period.slice(5) : period // MM-DD
}

// ── 返工收敛度量子面板 ────────────────────────────────────────────

function ReviewConvergencePanel() {
  const params = useParams<{ projectId?: string }>()
  const projectId = params.projectId
  const [metrics, setMetrics] = useState<ReviewMetrics | null>(null)
  const [loading, setLoading] = useState(false)
  const [discipline, setDiscipline] = useState<string | undefined>(undefined)

  const load = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      const res = await getReviewMetrics({ project_id: projectId, discipline })
      setMetrics(res?.success ? res.data : null)
    } catch {
      setMetrics(null)
    } finally {
      setLoading(false)
    }
  }, [projectId, discipline])

  useEffect(() => {
    load()
  }, [load])

  const disciplineRows = useMemo(
    () => Object.entries(metrics?.byDiscipline ?? {}),
    [metrics],
  )
  const categoryRows = useMemo(
    () =>
      Object.entries(metrics?.byCategory ?? {}).map(([key, stat]) => ({
        key,
        label: CATEGORY_LABELS[key] ?? key,
        ...stat,
      })),
    [metrics],
  )
  const trend = metrics?.trend ?? []
  const hasData =
    disciplineRows.length > 0 || categoryRows.length > 0 || trend.length > 0

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Text strong>返工收敛度量</Text>
        <Select
          size="small"
          allowClear
          placeholder="全部专业"
          style={{ width: 120 }}
          value={discipline}
          onChange={(value) => setDiscipline(value)}
          options={DISCIPLINE_OPTIONS.map((d) => ({ label: d, value: d }))}
        />
      </Space>

      {loading ? (
        <Spin size="small" />
      ) : !hasData ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无人审埋点数据"
        />
      ) : (
        <>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
              gap: 8,
            }}
          >
            {[
              { label: '确认率', value: metrics!.confirmRate, tone: '#52c41a' },
              { label: '改类率', value: metrics!.reclassRate, tone: '#faad14' },
              { label: '否定率', value: metrics!.rejectRate, tone: '#ff4d4f' },
              { label: '补框率', value: metrics!.addboxRate, tone: '#1677ff' },
            ].map((item) => (
              <div
                key={item.label}
                style={{
                  border: '1px solid #f0f0f0',
                  borderRadius: 6,
                  padding: '8px 10px',
                  background: '#fafafa',
                }}
              >
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {item.label}
                </Text>
                <div style={{ color: item.tone, fontWeight: 600, fontSize: 18 }}>
                  {pct(item.value)}
                </div>
              </div>
            ))}
          </div>

          {disciplineRows.length > 0 ? (
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                各专业返工率（rework = 改类+否定+补框）
              </Text>
              {disciplineRows.map(([name, stat]) => (
                <div key={name}>
                  <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                    <Text style={{ fontSize: 12 }}>{name}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {stat.rework}/{stat.total}
                    </Text>
                  </Space>
                  <Progress
                    percent={Number((stat.reworkRate * 100).toFixed(1))}
                    size="small"
                    strokeColor={reworkColor(stat.reworkRate)}
                  />
                </div>
              ))}
            </Space>
          ) : null}

          {categoryRows.length > 0 ? (
            <Table
              size="small"
              pagination={false}
              rowKey="key"
              dataSource={categoryRows}
              columns={[
                { title: '类别', dataIndex: 'label', key: 'label' },
                { title: '样本', dataIndex: 'total', key: 'total', width: 56 },
                {
                  title: '返工率',
                  key: 'reworkRate',
                  width: 80,
                  render: (_, row) => (
                    <Tag color={reworkColor(row.reworkRate)}>
                      {pct(row.reworkRate)}
                    </Tag>
                  ),
                },
              ]}
            />
          ) : null}

          {trend.length > 0 ? (
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                返工率收敛趋势（按天，越低越收敛）
              </Text>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'flex-end',
                  gap: 6,
                  height: 72,
                }}
              >
                {trend.map((point) => (
                  <div
                    key={point.period}
                    style={{ flex: 1, textAlign: 'center', minWidth: 0 }}
                    title={`${point.period}：${pct(point.reworkRate)}（${point.count} 条）`}
                  >
                    <div
                      style={{
                        height: `${Math.max(4, point.reworkRate * 56)}px`,
                        background: reworkColor(point.reworkRate),
                        borderRadius: 3,
                      }}
                    />
                    <div style={{ fontSize: 10, color: '#8c8c8c' }}>
                      {shortPeriod(point.period)}
                    </div>
                  </div>
                ))}
              </div>
            </Space>
          ) : null}

          <Alert
            type="info"
            showIcon
            message="度量口径"
            description="返工点 = 改类+否定+补框；确认 = 机器初模一次通过。返工率随数据/模型迭代下降，佐证「AI 出初模 → 人工审改」效率提升落在 25–30% 现实区间（非一键出 BIM）。"
          />
        </>
      )}
    </Space>
  )
}

// ── 主面板 ────────────────────────────────────────────────────────

export default function ModelQualityPanel({
  quality,
  buildingUnits,
  selectedScopeQuality,
}: ModelQualityPanelProps) {
  return (
    <div>
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 8,
          }}
        >
          {[
            `未分层 ${quality.unassignedStoryCount}`,
            `楼层冲突 ${quality.floorConflictCount}`,
            `低置信度单体 ${quality.lowConfidenceUnits.length}`,
            `待人工识别 ${quality.pendingManualCount}`,
            `待审语义 ${quality.pendingCandidateCount}`,
            `语义冲突 ${quality.semanticConflictCount}`,
          ].map((label) => (
            <div
              key={label}
              style={{
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                padding: '10px 12px',
                background: '#fafafa',
              }}
            >
              <Text strong>{label}</Text>
            </div>
          ))}
        </div>

        {quality.floorConflicts.length > 0 ? (
          <Alert
            type="warning"
            showIcon
            message="楼层冲突"
            description={
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {quality.floorConflicts.slice(0, 3).map((conflict) => (
                  <Text key={conflict.id}>
                    {unitLabel(buildingUnits, conflict.buildingUnitKey)}
                    {conflict.storyKey ? ` / ${conflict.storyKey}` : ''}:
                    {' '}
                    {conflict.message}
                  </Text>
                ))}
              </Space>
            }
          />
        ) : null}

        {quality.lowConfidenceUnits.length > 0 ? (
          <>
            <Divider style={{ margin: '4px 0' }} />
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <Text strong>低置信度单体</Text>
              <Space wrap>
                {quality.lowConfidenceUnits.map((unit) => (
                  <Tag key={unit.key} color="gold">
                    {unit.label}
                    {typeof unit.confidence === 'number'
                      ? ` ${Math.round(unit.confidence * 100)}%`
                      : ''}
                  </Tag>
                ))}
              </Space>
            </Space>
          </>
        ) : null}

        {selectedScopeQuality ? (
          <>
            <Divider style={{ margin: '4px 0' }} />
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Text strong>LOD 质量</Text>
              <Space wrap>
                <Tag color="geekblue">{selectedScopeQuality.scopeLabel}</Tag>
                {typeof selectedScopeQuality.level === 'number' ? (
                  <Tag color="blue">LOD {selectedScopeQuality.level}</Tag>
                ) : (
                  <Tag>LOD 未评定</Tag>
                )}
              </Space>

              {selectedScopeQuality.passedGates.length > 0 ? (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text type="secondary">已通过门槛</Text>
                  <Space wrap>
                    {selectedScopeQuality.passedGates.map((gate) => (
                      <Tag key={gate} color="green">
                        {gate}
                      </Tag>
                    ))}
                  </Space>
                </Space>
              ) : null}

              {selectedScopeQuality.missingEvidence.length > 0 ? (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text type="secondary">缺失证据</Text>
                  <Space wrap>
                    {selectedScopeQuality.missingEvidence.map((item) => (
                      <Tag key={item} color="orange">
                        {item}
                      </Tag>
                    ))}
                  </Space>
                </Space>
              ) : null}

              {selectedScopeQuality.degradationReasons.length > 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  message="降级原因"
                  description={selectedScopeQuality.degradationReasons.join('；')}
                />
              ) : null}

              {selectedScopeQuality.fallbackReasons.length > 0 ? (
                <Alert
                  type="info"
                  showIcon
                  message="回退说明"
                  description={selectedScopeQuality.fallbackReasons.join('；')}
                />
              ) : null}
            </Space>
          </>
        ) : null}

        <Divider style={{ margin: '4px 0' }} />
        <ReviewConvergencePanel />
      </Space>
    </div>
  )
}
