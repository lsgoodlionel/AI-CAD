import { Alert, Divider, Space, Tag, Typography } from 'antd'
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

function unitLabel(buildingUnits: BuildingUnitOption[], key?: string) {
  if (!key) return '未知单体'
  return buildingUnits.find((unit) => unit.key === key)?.label ?? key
}

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
      </Space>
    </div>
  )
}
