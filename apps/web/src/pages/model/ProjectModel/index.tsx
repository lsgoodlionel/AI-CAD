/**
 * 工程模型页（路由 /model 与 /model/:projectId）
 * - 无 projectId：项目选择卡片列表
 * - 有 projectId：顶部状态条 + 左侧楼层树/过滤器 + 中央 ModelViewer + 右侧详情 Drawer
 * - status=building 或触发重建后每 5s 轮询直到 ready/failed
 * - 404 MODEL_NOT_BUILT：空态引导「立即生成模型」
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { history, useParams, useSearchParams } from '@umijs/max'
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  List,
  Row,
  Segmented,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
// 项目列表数据源：项目独立接口已存在（services/projects.ts，套图审查页同款），
// 故不再用 listDrawings 聚合 project_id/project_name。
import { listProjects } from '@/services/projects'
import {
  getModelAssetUrl,
  getProjectModel,
  rebuildProjectModel,
} from '@/services/projectModel'
import type {
  ModelScene,
  ProjectModelResponse,
  ProjectModelStatus,
  SceneDrawing,
  SceneFloorV2,
  SceneMarker,
} from '@/services/projectModel'
import ModelViewer from './ModelViewer'
import type { RenderMode } from './ModelViewer'
import type { ElementUserData } from './elementsBuilder'

const { Text, Title } = Typography

const POLL_INTERVAL_MS = 5000

const DISCIPLINE_LABEL: Record<string, string> = {
  structure: '结构',
  architecture: '建筑',
  mep: '机电',
  decoration: '装修',
  other: '其他',
}

const SEVERITY_META: Record<string, { label: string; color: string }> = {
  critical: { label: '严重', color: '#f5222d' },
  major: { label: '较大', color: '#fa8c16' },
  minor: { label: '一般', color: '#faad14' },
  info: { label: '提示', color: '#8c8c8c' },
}

const ALL_SEVERITIES = ['critical', 'major', 'minor', 'info']

const MARKER_TYPE_LABEL: Record<string, string> = {
  issue: '图内问题',
  cross: '跨图发现',
}

const ALL_MARKER_TYPES = ['issue', 'cross']

const MODEL_STATUS_META: Record<
  ProjectModelStatus,
  { badge: 'processing' | 'success' | 'error'; text: string }
> = {
  building: { badge: 'processing', text: '构建中' },
  ready: { badge: 'success', text: '已就绪' },
  failed: { badge: 'error', text: '构建失败' },
}

type Selection =
  | { type: 'drawing'; drawing: SceneDrawing }
  | { type: 'marker'; marker: SceneMarker }
  | { type: 'element'; element: ElementUserData }

const ELEMENT_TYPE_LABEL: Record<string, string> = {
  columns: '柱',
  walls: '墙',
  beams: '梁',
  slabs: '板',
  equipment: '设备',
}

const RECONSTRUCTION_LABEL: Record<string, string> = {
  elements: '构件级重建',
  texture: '贴图级',
  mixed: '混合级',
}

/** 构件图层选项：固定四类 + 场景中实际出现的管线 system + 设备 */
function elementFilterOptions(scene: ModelScene): { label: string; value: string }[] {
  const systems = new Set<string>()
  for (const floor of scene.floors as SceneFloorV2[]) {
    for (const pipe of floor.elements?.pipes ?? []) systems.add(pipe.system)
  }
  return [
    ...['columns', 'walls', 'beams', 'slabs'].map((kind) => ({
      label: ELEMENT_TYPE_LABEL[kind], value: kind,
    })),
    ...Array.from(systems).map((system) => ({
      label: `管线·${system}`, value: `pipes:${system}`,
    })),
    { label: ELEMENT_TYPE_LABEL.equipment, value: 'equipment' },
  ]
}

interface RequestLikeError {
  response?: { status?: number }
}

function isNotBuiltError(error: unknown): boolean {
  return (error as RequestLikeError)?.response?.status === 404
}

// ── 项目选择列表（无 projectId 时）────────────────────────────

interface ProjectOption {
  id: string
  name: string
  code?: string
}

function ProjectPicker() {
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listProjects({ limit: 200 })
      .then((res: { items?: ProjectOption[] }) => setProjects(res.items ?? []))
      .catch(() => message.error('项目列表加载失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin tip="加载项目列表…" />
      </div>
    )
  }

  if (projects.length === 0) {
    return <Empty style={{ marginTop: 80 }} description="暂无项目" />
  }

  return (
    <div style={{ padding: 16 }}>
      <Title level={5}>选择项目查看工程模型</Title>
      <Row gutter={[16, 16]}>
        {projects.map((project) => (
          <Col key={project.id} xs={24} sm={12} md={8} lg={6}>
            <Card
              hoverable
              onClick={() => history.push(`/model/${project.id}`)}
              size="small"
            >
              <Space direction="vertical" size={4}>
                <Text strong>{project.name}</Text>
                {project.code ? <Text type="secondary">{project.code}</Text> : null}
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  )
}

// ── 模型查看主页面（有 projectId 时）──────────────────────────

interface ModelWorkspaceProps {
  projectId: string
  focusDrawingId?: string
}

function ModelWorkspace({ projectId, focusDrawingId }: ModelWorkspaceProps) {
  const [model, setModel] = useState<ProjectModelResponse | null>(null)
  const [isNotBuilt, setIsNotBuilt] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [isRebuilding, setIsRebuilding] = useState(false)

  const [disciplineFilter, setDisciplineFilter] = useState<string[]>([])
  const [severityFilter, setSeverityFilter] = useState<string[]>(ALL_SEVERITIES)
  const [markerTypeFilter, setMarkerTypeFilter] = useState<string[]>(ALL_MARKER_TYPES)
  const [isolatedFloorKey, setIsolatedFloorKey] = useState<string | null>(null)
  const [selection, setSelection] = useState<Selection | null>(null)
  // ── V2 构件级视图状态 ──
  const [renderMode, setRenderMode] = useState<RenderMode>('mixed')
  const [elementFilter, setElementFilter] = useState<string[] | undefined>(undefined)
  const [selectedBuildingKey, setSelectedBuildingKey] = useState<string | null>(null)

  const scene: ModelScene | null = model?.scene ?? null
  const isV2 = scene?.schema_version === 2
  const buildings = scene?.buildings ?? []

  // 选中单体 → 派生只含该单体楼层/标记的 scene（切换单体重建场景）
  const viewScene: ModelScene | null = useMemo(() => {
    if (!scene) return null
    if (!isV2 || !selectedBuildingKey) return scene
    const building = buildings.find((b) => b.key === selectedBuildingKey)
    if (!building) return scene
    return {
      ...scene,
      floors: building.floors,
      markers: scene.markers.filter(
        (marker) => (marker.building_key ?? 'main') === selectedBuildingKey,
      ),
    }
  }, [scene, isV2, selectedBuildingKey, buildings])

  const fetchModel = useCallback(async () => {
    try {
      const res = await getProjectModel(projectId)
      setModel(res)
      setIsNotBuilt(false)
    } catch (error: unknown) {
      if (isNotBuiltError(error)) {
        setIsNotBuilt(true)
        setModel(null)
      } else {
        message.error('模型加载失败，请稍后重试')
      }
    } finally {
      setIsLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    setIsLoading(true)
    setModel(null)
    setIsNotBuilt(false)
    setSelection(null)
    setIsolatedFloorKey(null)
    fetchModel()
  }, [fetchModel])

  // building 状态每 5s 轮询直到 ready / failed
  useEffect(() => {
    if (model?.status !== 'building') return undefined
    const timer = setTimeout(fetchModel, POLL_INTERVAL_MS)
    return () => clearTimeout(timer)
  }, [model?.status, model?.version, fetchModel])

  // 场景变化时初始化专业过滤器为全选
  const availableDisciplines = useMemo(() => {
    if (!scene) return []
    const set = new Set<string>()
    scene.floors.forEach((floor) =>
      floor.drawings.forEach((drawing) => set.add(drawing.discipline)),
    )
    return Array.from(set)
  }, [scene])

  useEffect(() => {
    setDisciplineFilter(availableDisciplines)
  }, [availableDisciplines])

  const handleRebuild = async () => {
    setIsRebuilding(true)
    try {
      await rebuildProjectModel(projectId)
      message.success('已触发模型重建，构建完成后自动刷新')
      setIsNotBuilt(false)
      await fetchModel()
    } catch {
      // 全局 errorHandler 已提示，这里仅兜底
      message.error('触发重建失败')
    } finally {
      setIsRebuilding(false)
    }
  }

  const resolveAssetUrl = useCallback(
    async (key: string) => {
      const res = await getModelAssetUrl(projectId, key)
      return res.url
    },
    [projectId],
  )

  const allDrawings = useMemo(
    () => (scene ? scene.floors.flatMap((floor) => floor.drawings) : []),
    [scene],
  )

  const markerDrawing = useMemo(() => {
    if (selection?.type !== 'marker') return null
    return (
      allDrawings.find((d) => d.drawing_id === selection.marker.ref.drawing_id) ?? null
    )
  }, [selection, allDrawings])

  const sortedFloors = useMemo(
    () => (viewScene ? [...viewScene.floors].sort((a, b) => b.order - a.order) : []),
    [viewScene],
  )

  // ── 未构建空态 ─────────────────────────────────────────────
  if (isNotBuilt) {
    return (
      <div style={{ textAlign: 'center', paddingTop: 100 }}>
        <Empty description="该项目尚未生成工程模型">
          <Button type="primary" loading={isRebuilding} onClick={handleRebuild}>
            立即生成模型
          </Button>
        </Empty>
      </div>
    )
  }

  if (isLoading || !model) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin tip="加载工程模型…" />
      </div>
    )
  }

  const statusMeta = MODEL_STATUS_META[model.status]

  return (
    <div style={{ padding: 12 }}>
      {/* ── 顶部状态条 ── */}
      <Card size="small" style={{ marginBottom: 12 }}>
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
            onClick={handleRebuild}
          >
            重建模型
          </Button>
          {scene ? (
            <>
              <Divider type="vertical" />
              <Text>图纸 {scene.stats.total_drawings} 张</Text>
              <Text>问题 {scene.stats.total_issues} 个</Text>
              <Text>楼层 {scene.stats.floors} 层</Text>
              {isV2 && scene.stats.reconstruction ? (
                <Tooltip title="构件级重建：由矢量图纸提取真实几何生成；扫描图纸楼层自动回退贴图">
                  <Tag color={scene.stats.reconstruction === 'texture' ? 'default' : 'geekblue'}>
                    {RECONSTRUCTION_LABEL[scene.stats.reconstruction]}
                  </Tag>
                </Tooltip>
              ) : null}
              {isV2 && scene.stats.elements_total ? (
                <Text type="secondary">
                  构件{' '}
                  {Object.entries(scene.stats.elements_total)
                    .filter(([, count]) => count > 0)
                    .map(([kind, count]) => `${ELEMENT_TYPE_LABEL[kind] ?? '管线'}${count}`)
                    .join(' / ') || '—'}
                </Text>
              ) : null}
              {isV2 ? (
                <Segmented
                  size="small"
                  value={renderMode}
                  onChange={(value) => setRenderMode(value as RenderMode)}
                  options={[
                    { label: '构件', value: 'elements' },
                    { label: '贴图', value: 'texture' },
                    { label: '混合', value: 'mixed' },
                  ]}
                />
              ) : null}
              {Object.entries(scene.stats.by_severity).map(([severity, count]) => {
                const meta = SEVERITY_META[severity]
                return meta ? (
                  <Tag key={severity} color={meta.color}>
                    {meta.label} {count}
                  </Tag>
                ) : null
              })}
            </>
          ) : null}
        </Space>
        {model.status === 'building' ? (
          <Alert
            style={{ marginTop: 8 }}
            type="info"
            showIcon
            message="模型构建中，页面将每 5 秒自动刷新…"
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

      {scene ? (
        <Row gutter={12}>
          {/* ── 左侧：单体 + 楼层树 + 过滤器 ── */}
          <Col flex="260px">
            {isV2 && buildings.length > 1 ? (
              <Card size="small" title="单体（点击聚焦，再点回总体）" style={{ marginBottom: 12 }}>
                <List
                  size="small"
                  dataSource={buildings}
                  renderItem={(building) => {
                    const isActive = selectedBuildingKey === building.key
                    return (
                      <List.Item
                        onClick={() => {
                          setSelectedBuildingKey(isActive ? null : building.key)
                          setIsolatedFloorKey(null)
                        }}
                        style={{
                          cursor: 'pointer', paddingLeft: 8, paddingRight: 8,
                          background: isActive ? '#e6f4ff' : undefined, borderRadius: 6,
                        }}
                      >
                        <Space>
                          <Text strong={isActive}>{building.label || building.key}</Text>
                          <Text type="secondary">{building.floors.length} 层</Text>
                        </Space>
                      </List.Item>
                    )
                  }}
                />
              </Card>
            ) : null}

            <Card size="small" title="楼层（点击隔离，再点取消）" style={{ marginBottom: 12 }}>
              <List
                size="small"
                dataSource={sortedFloors}
                renderItem={(floor) => {
                  const isActive = isolatedFloorKey === floor.key
                  return (
                    <List.Item
                      onClick={() =>
                        setIsolatedFloorKey(isActive ? null : floor.key)
                      }
                      style={{
                        cursor: 'pointer',
                        paddingLeft: 8,
                        paddingRight: 8,
                        background: isActive ? '#e6f4ff' : undefined,
                        borderRadius: 6,
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
            </Card>

            <Card size="small" title="专业" style={{ marginBottom: 12 }}>
              <Checkbox.Group
                style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                value={disciplineFilter}
                onChange={(values) => setDisciplineFilter(values as string[])}
                options={availableDisciplines.map((discipline) => ({
                  label: DISCIPLINE_LABEL[discipline] ?? discipline,
                  value: discipline,
                }))}
              />
            </Card>

            <Card size="small" title="严重度" style={{ marginBottom: 12 }}>
              <Checkbox.Group
                style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                value={severityFilter}
                onChange={(values) => setSeverityFilter(values as string[])}
                options={ALL_SEVERITIES.map((severity) => ({
                  label: SEVERITY_META[severity].label,
                  value: severity,
                }))}
              />
            </Card>

            <Card size="small" title="标记类型" style={{ marginBottom: 12 }}>
              <Checkbox.Group
                style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                value={markerTypeFilter}
                onChange={(values) => setMarkerTypeFilter(values as string[])}
                options={ALL_MARKER_TYPES.map((type) => ({
                  label: MARKER_TYPE_LABEL[type],
                  value: type,
                }))}
              />
            </Card>

            {isV2 && viewScene ? (
              <Card size="small" title="构件图层">
                <Checkbox.Group
                  style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                  value={elementFilter ?? elementFilterOptions(viewScene).map((o) => o.value)}
                  onChange={(values) => setElementFilter(values as string[])}
                  options={elementFilterOptions(viewScene)}
                />
              </Card>
            ) : null}
          </Col>

          {/* ── 中央：3D 查看器 ── */}
          <Col flex="auto">
            <Card size="small" styles={{ body: { padding: 0 } }}>
              <div style={{ height: 'calc(100vh - 300px)', minHeight: 480 }}>
                <ModelViewer
                  scene={viewScene ?? scene}
                  focusDrawingId={focusDrawingId}
                  disciplineFilter={disciplineFilter}
                  severityFilter={severityFilter}
                  markerTypeFilter={markerTypeFilter}
                  isolatedFloorKey={isolatedFloorKey}
                  renderMode={renderMode}
                  elementFilter={elementFilter}
                  resolveAssetUrl={resolveAssetUrl}
                  onSelectDrawing={(drawing) => setSelection({ type: 'drawing', drawing })}
                  onSelectMarker={(marker) => setSelection({ type: 'marker', marker })}
                  onSelectElement={(element) => setSelection({ type: 'element', element })}
                />
              </div>
            </Card>
          </Col>
        </Row>
      ) : (
        <Empty description="模型场景为空，请尝试重建" />
      )}

      {/* ── 右侧：详情 Drawer ── */}
      <Drawer
        open={selection !== null}
        onClose={() => setSelection(null)}
        width={380}
        title={
          selection?.type === 'drawing'
            ? '图纸信息'
            : selection?.type === 'element'
              ? '构件信息'
              : '问题标记'
        }
      >
        {selection?.type === 'drawing' ? (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="图号">{selection.drawing.drawing_no}</Descriptions.Item>
              <Descriptions.Item label="图名">{selection.drawing.title}</Descriptions.Item>
              <Descriptions.Item label="专业">
                {DISCIPLINE_LABEL[selection.drawing.discipline] ?? selection.drawing.discipline}
              </Descriptions.Item>
              <Descriptions.Item label="当前阶段">
                {selection.drawing.current_stage}
              </Descriptions.Item>
              <Descriptions.Item label="问题数">
                {selection.drawing.issue_count}
                {selection.drawing.critical_count > 0 ? (
                  <Tag color="#f5222d" style={{ marginLeft: 8 }}>
                    严重 {selection.drawing.critical_count}
                  </Tag>
                ) : null}
              </Descriptions.Item>
            </Descriptions>
            <Button
              type="primary"
              block
              style={{ marginTop: 16 }}
              onClick={() => history.push(`/drawings/${selection.drawing.drawing_id}`)}
            >
              进入图纸详情
            </Button>
          </>
        ) : null}

        {selection?.type === 'marker' ? (
          <>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <div>
                <Tag color={SEVERITY_META[selection.marker.severity]?.color}>
                  {SEVERITY_META[selection.marker.severity]?.label ?? selection.marker.severity}
                </Tag>
                <Tag>{MARKER_TYPE_LABEL[selection.marker.type] ?? selection.marker.type}</Tag>
              </div>
              <Text>{selection.marker.title}</Text>
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="楼层">{selection.marker.floor_key}</Descriptions.Item>
                <Descriptions.Item label="专业代码">
                  {selection.marker.discipline_code || '—'}
                </Descriptions.Item>
                <Descriptions.Item label="所属图纸">
                  {markerDrawing
                    ? `${markerDrawing.drawing_no} ${markerDrawing.title}`
                    : selection.marker.ref.drawing_id || '—'}
                </Descriptions.Item>
              </Descriptions>
            </Space>
            {selection.marker.ref.drawing_id ? (
              <Button
                type="primary"
                block
                style={{ marginTop: 16 }}
                onClick={() => history.push(`/drawings/${selection.marker.ref.drawing_id}`)}
              >
                查看图纸
              </Button>
            ) : null}
          </>
        ) : null}
        {selection?.type === 'element' ? (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="构件类型">
                {selection.element.elementType.startsWith('pipes:')
                  ? `管线·${selection.element.elementType.slice(6)}`
                  : ELEMENT_TYPE_LABEL[selection.element.elementType] ?? selection.element.elementType}
              </Descriptions.Item>
              <Descriptions.Item label="所在楼层">{selection.element.floorKey}</Descriptions.Item>
              <Descriptions.Item label="数量">
                {selection.element.count}
                {selection.element.count > 1 ? '（同类合批渲染）' : ''}
              </Descriptions.Item>
              {selection.element.label ? (
                <Descriptions.Item label="标注">{selection.element.label}</Descriptions.Item>
              ) : null}
            </Descriptions>
            <Alert
              style={{ marginTop: 12 }}
              type="info"
              showIcon
              message="构件级重建（矢量图纸提取）"
              description="几何由结构/机电平面图矢量线条确定性识别生成，构件可追溯来源图纸。"
            />
            {selection.element.src ? (
              <Button
                type="primary"
                block
                style={{ marginTop: 16 }}
                onClick={() => history.push(`/drawings/${selection.element.src}`)}
              >
                查看来源图纸
              </Button>
            ) : null}
          </>
        ) : null}
      </Drawer>
    </div>
  )
}

// ── 路由入口 ─────────────────────────────────────────────────

export default function ProjectModelPage() {
  const params = useParams<{ projectId?: string }>()
  const [searchParams] = useSearchParams()
  const focusDrawingId = searchParams.get('focus') ?? undefined

  if (!params.projectId) {
    return <ProjectPicker />
  }
  return <ModelWorkspace projectId={params.projectId} focusDrawingId={focusDrawingId} />
}
