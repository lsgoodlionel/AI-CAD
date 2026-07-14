/**
 * 工程模型页顶部状态条：状态/版本/重建 + LOD 切换 + 渲染模式 + 统计 Tag +
 * 浏览/审校/算量三视图模式切换（D-13）。从原 index.tsx 头部 Card 迁出。
 */
import { Alert, Badge, Button, Card, Divider, Progress, Segmented, Space, Tag, Tooltip, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ModelScene, ProjectModelResponse } from '@/services/projectModel'
import type { ModelViewMode } from './sceneBuilder'
import type { LodModeOption, ModelLodMode } from './types'
import type { WorkspaceMode } from './useModelWorkspaceState'
import { MODEL_STATUS_META, RECONSTRUCTION_LABEL, SEVERITY_META } from './modelWorkspaceConstants'

const { Text } = Typography

const ELEMENT_TYPE_LABEL: Record<string, string> = {
  columns: '柱', walls: '墙', beams: '梁', slabs: '板', equipment: '设备',
}

const MODE_OPTIONS: { label: string; value: WorkspaceMode }[] = [
  { label: '浏览', value: 'browse' },
  { label: '审校', value: 'review' },
  { label: '算量', value: 'quantity' },
]

interface ModelHeaderBarProps {
  mode: WorkspaceMode
  onModeChange: (mode: WorkspaceMode) => void
  model: ProjectModelResponse
  scene: ModelScene | null
  isRebuilding: boolean
  onRebuild: () => void
  pendingManualCount: number
  lodModes: LodModeOption[]
  lodMode: ModelLodMode
  onLodModeChange: (mode: ModelLodMode) => void
  viewModeOptions: { label: string; value: ModelViewMode }[]
  viewMode: ModelViewMode
  onViewModeChange: (mode: ModelViewMode) => void
}

export default function ModelHeaderBar({
  mode,
  onModeChange,
  model,
  scene,
  isRebuilding,
  onRebuild,
  pendingManualCount,
  lodModes,
  lodMode,
  onLodModeChange,
  viewModeOptions,
  viewMode,
  onViewModeChange,
}: ModelHeaderBarProps) {
  const statusMeta = MODEL_STATUS_META[model.status]

  return (
    <Card size="small" style={{ marginBottom: 12 }}>
      <Space size="middle" wrap style={{ width: '100%', justifyContent: 'space-between' }}>
        <Space size="middle" wrap>
          <Badge status={statusMeta.badge} text={statusMeta.text} />
          <Text type="secondary">版本 v{model.version}</Text>
          {model.built_at ? (
            <Text type="secondary">构建于 {new Date(model.built_at).toLocaleString()}</Text>
          ) : null}
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={isRebuilding}
            disabled={model.status === 'building'}
            onClick={onRebuild}
          >
            重建模型
          </Button>
          {scene ? (
            <>
              <Divider type="vertical" />
              <Text>图纸 {scene.stats.total_drawings} 张</Text>
              <Text>问题 {scene.stats.total_issues} 个</Text>
              <Text>楼层 {scene.stats.floors} 层</Text>
              {pendingManualCount > 0 ? <Tag color="gold">待人工识别 {pendingManualCount}</Tag> : null}
              {scene.stats.reconstruction ? (
                <Tooltip title="构件级重建、贴图级与混合模式保持可用；LOD 入口单独控制审图骨架/建筑体量/实景近似。">
                  <Tag color={scene.stats.reconstruction === 'texture' ? 'default' : 'geekblue'}>
                    {RECONSTRUCTION_LABEL[scene.stats.reconstruction]}
                  </Tag>
                </Tooltip>
              ) : null}
              {scene.schema_version === 2 && scene.stats.elements_total ? (
                <Text type="secondary">
                  构件{' '}
                  {Object.entries(scene.stats.elements_total)
                    .filter(([, count]) => count > 0)
                    .map(([kind, count]) => `${ELEMENT_TYPE_LABEL[kind] ?? '管线'}${count}`)
                    .join(' / ') || '—'}
                </Text>
              ) : null}
              {Object.entries(scene.stats.by_severity).map(([severity, count]) => {
                const meta = SEVERITY_META[severity]
                return meta ? (
                  <Tag key={severity} color={meta.color}>{meta.label} {count}</Tag>
                ) : null
              })}
            </>
          ) : null}
        </Space>

        <Segmented value={mode} onChange={(value) => onModeChange(value as WorkspaceMode)} options={MODE_OPTIONS} />
      </Space>

      <Space size={8} wrap style={{ marginTop: 12 }}>
        {lodModes.map((item) => {
          const button = (
            <Button
              key={item.key}
              type={lodMode === item.key ? 'primary' : 'default'}
              disabled={!item.enabled}
              onClick={() => onLodModeChange(item.key)}
            >
              {item.label}
            </Button>
          )
          return item.enabled
            ? button
            : (
              <Tooltip key={item.key} title={item.reason ?? '当前数据暂不支持'}>
                <span>{button}</span>
              </Tooltip>
            )
        })}
        {viewModeOptions.length > 1 ? (
          <Segmented
            size="small"
            value={viewMode}
            onChange={(value) => onViewModeChange(value as ModelViewMode)}
            options={viewModeOptions}
          />
        ) : null}
      </Space>

      {model.status === 'building' ? (
        <Alert
          style={{ marginTop: 8 }}
          type="info"
          showIcon
          message={
            model.progress
              ? `${model.progress.stage_label}${model.progress.current ? `：${model.progress.current}` : ''}`
              : '模型构建中，页面将每 5 秒自动刷新…'
          }
          description={
            model.progress && model.progress.total > 1 ? (
              <Progress
                percent={Math.round((model.progress.done / model.progress.total) * 100)}
                size="small"
                status="active"
                format={() => `${model.progress?.done}/${model.progress?.total}`}
              />
            ) : undefined
          }
        />
      ) : null}
      {model.status === 'failed' ? (
        <Alert
          style={{ marginTop: 8 }}
          type="error"
          showIcon
          message="模型构建失败"
          description={model.error ?? '未知错误，请尝试重建'}
        />
      ) : null}
    </Card>
  )
}
