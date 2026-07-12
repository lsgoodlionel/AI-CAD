/**
 * 工程模型页（路由 /model 与 /model/:projectId）
 * - 无 projectId：项目选择卡片列表
 * - 有 projectId：顶部状态条 + 左侧楼层树/过滤器 + 中央 ModelViewer + 右侧质量/标注工作台
 * - status=building 或触发重建后每 5s 轮询直到 ready/failed
 * - 404 MODEL_NOT_BUILT：空态引导「立即生成模型」
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { history, request, useParams, useSearchParams } from '@umijs/max'
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
  Progress,
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
import { listProjects } from '@/services/projects'
import {
  applyProjectModelSemanticOperation,
  getModelAssetUrl,
  getProjectModel,
  getProjectModelSemanticGraph,
  previewProjectModelSemanticImpact,
  rebuildProjectModel,
} from '@/services/projectModel'
import type {
  ModelScene,
  SemanticOperationRequest,
  ProjectModelResponse,
  ProjectModelStatus,
  SceneDrawing,
  SceneFloorV2,
  SceneMarker,
} from '@/services/projectModel'
import CollapsiblePanel from './CollapsiblePanel'
import DrawingAnnotationQueue from './DrawingAnnotationQueue'
import ModelQualityPanel from './ModelQualityPanel'
import StoryHeightPanel from './StoryHeightPanel'
import ModelViewer from './ModelViewer'
import FragmentsScene from './FragmentsScene'
import SemanticReviewQueue from './SemanticReviewQueue'
import SemanticTreePanel from './SemanticTreePanel'
import type { RenderMode } from './ModelViewer'
import type {
  FragmentPickResult,
  FragmentsCameraPose,
  FragmentsSceneHandle,
} from './FragmentsScene'
import type { ElementUserData } from './elementsBuilder'
import type { ModelViewMode } from './sceneBuilder'
import FragmentPropertyPanel from './FragmentPropertyPanel'
import { findSemanticNodeForItem, resolvePickedItem } from './fragmentsPicking'
import type { PickedFragmentItem } from '@/services/projectModel'
import { pickDefaultViewMode, readModelIfc } from './sceneBuilder'
import {
  buildStoryOptions,
  normalizeModelInsights,
  resolveScopeLodQuality,
} from './modelData'
import type {
  AnnotationQueueItem,
  AnnotationSaveDraft,
  BuildingUnitOption,
  LodModeOption,
  ModelLodMode,
  ModelQualitySummary,
  SemanticOperationDraft,
  SemanticOperationOutcome,
  SemanticOperationPreview,
  SemanticTreeNodeView,
} from './types'

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

const EMPTY_QUALITY: ModelQualitySummary = {
  unassignedStoryCount: 0,
  floorConflictCount: 0,
  floorConflicts: [],
  lowConfidenceUnits: [],
  pendingManualCount: 0,
  pendingCandidateCount: 0,
  semanticConflictCount: 0,
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
    { label: '外观壳体', value: 'shell' },
  ]
}

interface RequestLikeError {
  response?: { status?: number, data?: Record<string, unknown> }
  data?: Record<string, unknown>
  info?: Record<string, unknown>
}

function isNotBuiltError(error: unknown): boolean {
  return (error as RequestLikeError)?.response?.status === 404
}

function readErrorNumber(error: unknown, ...keys: string[]): number | undefined {
  const data = (error as RequestLikeError)?.data
  const info = (error as RequestLikeError)?.info
  const responseData = (error as RequestLikeError)?.response?.data
  const sources = [
    data,
    data?.detail as Record<string, unknown> | undefined,
    (data?.detail as Record<string, unknown> | undefined)?.latest as Record<string, unknown> | undefined,
    info,
    info?.detail as Record<string, unknown> | undefined,
    (info?.detail as Record<string, unknown> | undefined)?.latest as Record<string, unknown> | undefined,
    responseData,
    responseData?.detail as Record<string, unknown> | undefined,
    (responseData?.detail as Record<string, unknown> | undefined)?.latest as Record<string, unknown> | undefined,
  ]
  for (const source of sources) {
    if (!source) continue
    for (const key of keys) {
      const value = source[key]
      if (typeof value === 'number' && Number.isFinite(value)) return value
      if (typeof value === 'string' && value.trim()) {
        const parsed = Number(value)
        if (Number.isFinite(parsed)) return parsed
      }
    }
  }
  return undefined
}

function readErrorString(error: unknown, ...keys: string[]): string | undefined {
  const data = (error as RequestLikeError)?.data
  const info = (error as RequestLikeError)?.info
  const responseData = (error as RequestLikeError)?.response?.data
  const sources = [
    data,
    data?.detail as Record<string, unknown> | undefined,
    info,
    info?.detail as Record<string, unknown> | undefined,
    responseData,
    responseData?.detail as Record<string, unknown> | undefined,
  ]
  for (const source of sources) {
    if (!source) continue
    for (const key of keys) {
      const value = source[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
  }
  return undefined
}

function normalizeManualKey(input: string): string {
  const value = input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return value || `manual-${Date.now()}`
}

function mergeManualBuildingUnit(
  current: BuildingUnitOption[],
  draft: AnnotationSaveDraft,
): BuildingUnitOption[] {
  if (!draft.buildingUnitName.trim()) return current
  const existing = current.find((unit) => unit.label === draft.buildingUnitName.trim())
  if (existing) return current
  return [
    ...current,
    {
      key: draft.buildingUnitKey ?? normalizeManualKey(draft.buildingUnitName),
      label: draft.buildingUnitName.trim(),
      source: 'manual',
      hasGeometry: false,
    },
  ].sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
}

async function saveModelAnnotation(
  projectId: string,
  item: AnnotationQueueItem,
  draft: AnnotationSaveDraft,
) {
  const payload = {
    drawing_id: item.drawingId,
    building_unit_key: draft.buildingUnitKey,
    building_unit_name: draft.buildingUnitName.trim(),
    story_key: draft.storyKey,
    story_name: draft.storyName.trim(),
    drawing_type: draft.drawingType.trim(),
  }
  const endpoints = [
    `/api/v1/projects/${projectId}/model/annotations`,
    `/api/v1/projects/${projectId}/model/annotation-queue`,
  ]

  for (let index = 0; index < endpoints.length; index += 1) {
    try {
      await request(endpoints[index], {
        method: 'POST',
        data: payload,
        skipErrorHandler: true,
      })
      return
    } catch (error) {
      const status = (error as RequestLikeError)?.response?.status
      if (status === 404 && index < endpoints.length - 1) continue
      throw error
    }
  }
}

function semanticOperationPayload(
  draft: SemanticOperationDraft,
): SemanticOperationRequest {
  return {
    operation: draft.operation,
    node_id: draft.nodeId,
    version: draft.version,
    target_node_id: draft.targetNodeId,
    new_name: draft.newName,
    split_names: draft.splitNames,
  }
}

function activeLodMode(
  lodModes: LodModeOption[],
  lodMode: ModelLodMode,
): LodModeOption {
  return lodModes.find((item) => item.key === lodMode)
    ?? lodModes.find((item) => item.enabled)
    ?? lodModes[0]
}

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
  const [viewMode, setViewMode] = useState<ModelViewMode>('mixed')
  const fragmentsSceneRef = useRef<FragmentsSceneHandle>(null)
  /** 拾取请求令牌：连点/清除时旧的 resolvePickedItem 迟到不覆盖新状态。 */
  const pickRequestRef = useRef(0)
  const [fragmentItem, setFragmentItem] = useState<PickedFragmentItem | null>(null)
  const [fragmentItemLoading, setFragmentItemLoading] = useState(false)
  const fragmentsCameraPose = useRef<FragmentsCameraPose | null>(null)
  const [elementFilter, setElementFilter] = useState<string[] | undefined>(undefined)
  const [selectedBuildingKey, setSelectedBuildingKey] = useState<string | null>(null)
  const [selectedSemanticNode, setSelectedSemanticNode] = useState<SemanticTreeNodeView | null>(null)
  const [buildingUnits, setBuildingUnits] = useState<BuildingUnitOption[]>([])
  const [annotationQueue, setAnnotationQueue] = useState<AnnotationQueueItem[]>([])
  const [lodMode, setLodMode] = useState<ModelLodMode>('review_skeleton')

  const scene: ModelScene | null = model?.scene ?? null
  const isV2 = scene?.schema_version === 2
  const modelIfc = useMemo(() => (scene ? readModelIfc(scene) : null), [scene])
  const fragKey = modelIfc?.frag_key ?? null
  const insights = useMemo(() => (model ? normalizeModelInsights(model) : null), [model])
  const lodModes = insights?.lodModes ?? [
    { key: 'review_skeleton', label: '审图骨架', enabled: true },
    { key: 'architectural_massing', label: '建筑体量', enabled: true },
    { key: 'realistic_proxy', label: '实景近似', enabled: true },
  ]
  const currentLod = activeLodMode(lodModes, lodMode)
  const semanticTreeGroups = insights?.semanticTreeGroups ?? []
  const semanticNodeMap = insights?.semanticNodeMap ?? {}
  const semanticReviewQueue = insights?.semanticReviewQueue ?? []

  // 渲染模式候选：ifc(Fragments) 优先展示，V2 追加构件/贴图/混合
  const viewModeOptions = useMemo(() => {
    const options: { label: string; value: ModelViewMode }[] = []
    if (fragKey) options.push({ label: 'IFC 模型', value: 'ifc' })
    if (isV2) {
      options.push({ label: '构件', value: 'elements' })
      options.push({ label: '贴图', value: 'texture' })
      options.push({ label: '混合', value: 'mixed' })
    }
    return options
  }, [fragKey, isV2])

  // 模型身份稳定后设一次默认模式（frag_key/schema 不变则不覆盖用户手动选择）
  useEffect(() => {
    if (!scene) return
    setViewMode(pickDefaultViewMode(scene))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene?.project.id, fragKey, scene?.schema_version])

  // A-08 接线：点击构件 → 高亮 + 解析 IFC 属性 → 属性面板
  const handleFragmentPick = useCallback((pick: FragmentPickResult | null) => {
    const sceneHandle = fragmentsSceneRef.current
    if (!sceneHandle) return
    pickRequestRef.current += 1
    const requestToken = pickRequestRef.current
    if (!pick) {
      void sceneHandle.clearHighlight().catch(() => {})
      setFragmentItem(null)
      setFragmentItemLoading(false)
      return
    }
    void sceneHandle.highlight([pick.localId]).catch(() => {})
    const model = sceneHandle.getModel()
    if (!model) return
    setFragmentItemLoading(true)
    resolvePickedItem(model, pick.localId)
      .then((resolved) => {
        if (pickRequestRef.current !== requestToken) return
        setFragmentItem(resolved)
      })
      .catch(() => {
        if (pickRequestRef.current !== requestToken) return
        setFragmentItem(null)
      })
      .finally(() => {
        if (pickRequestRef.current !== requestToken) return
        setFragmentItemLoading(false)
      })
  }, [])

  // 离开 IFC 模式清空选中构件，避免残留
  useEffect(() => {
    if (viewMode !== 'ifc') setFragmentItem(null)
  }, [viewMode])

  useEffect(() => {
    if (!insights) {
      setBuildingUnits([])
      setAnnotationQueue([])
      return
    }
    setBuildingUnits(insights.buildingUnits)
    setAnnotationQueue(insights.annotationQueue)
  }, [insights])

  useEffect(() => {
    if (!lodModes.some((item) => item.key === lodMode && item.enabled)) {
      const next = lodModes.find((item) => item.enabled)
      if (next) setLodMode(next.key)
    }
  }, [lodMode, lodModes])

  useEffect(() => {
    if (!selectedSemanticNode) return
    const nextNode = semanticNodeMap[selectedSemanticNode.id]
    if (!nextNode) {
      setSelectedSemanticNode(null)
      return
    }
    if (nextNode !== selectedSemanticNode) {
      setSelectedSemanticNode(nextNode)
    }
  }, [selectedSemanticNode, semanticNodeMap])

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

  const selectedBuilding = buildingUnits.find((unit) => unit.key === selectedBuildingKey) ?? null
  const viewScene: ModelScene | null = useMemo(() => {
    if (!scene || !selectedBuildingKey) return scene
    const building = scene.buildings?.find((item) => item.key === selectedBuildingKey)
    if (!building) return scene
    return {
      ...scene,
      floors: building.floors,
      markers: scene.markers.filter((marker) => marker.building_key === selectedBuildingKey),
    }
  }, [scene, selectedBuildingKey])

  const storyOptionsByBuilding = useMemo(
    () => buildStoryOptions(scene, buildingUnits),
    [scene, buildingUnits],
  )

  const quality = useMemo<ModelQualitySummary>(() => {
    if (!insights) return EMPTY_QUALITY
    return {
      ...insights.quality,
      pendingManualCount: annotationQueue.length,
    }
  }, [insights, annotationQueue.length])
  const selectedScopeQuality = useMemo(
    () => (
      insights
        ? resolveScopeLodQuality(
          insights,
          selectedSemanticNode?.id ?? selectedBuildingKey,
        )
        : null
    ),
    [insights, selectedBuildingKey, selectedSemanticNode],
  )

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

  const refreshSemanticGraph = useCallback(async () => {
    try {
      const semanticTree = await getProjectModelSemanticGraph(projectId)
      setModel((current) => (current ? { ...current, semantic_tree: semanticTree } : current))
    } catch {
      await fetchModel()
    }
  }, [fetchModel, projectId])

  useEffect(() => {
    setIsLoading(true)
    setModel(null)
    setIsNotBuilt(false)
    setSelection(null)
    setIsolatedFloorKey(null)
    setSelectedSemanticNode(null)
    fetchModel()
  }, [fetchModel])

  useEffect(() => {
    if (model?.status !== 'building') return undefined
    const timer = setTimeout(fetchModel, POLL_INTERVAL_MS)
    return () => clearTimeout(timer)
  }, [model, fetchModel])

  const handleRebuild = async () => {
    setIsRebuilding(true)
    try {
      await rebuildProjectModel(projectId)
      message.success('已触发模型重建，构建完成后自动刷新')
      setIsNotBuilt(false)
      await fetchModel()
    } catch {
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
    return allDrawings.find((d) => d.drawing_id === selection.marker.ref.drawing_id) ?? null
  }, [selection, allDrawings])

  const sortedFloors = useMemo(
    () => (viewScene ? [...viewScene.floors].sort((a, b) => b.order - a.order) : []),
    [viewScene],
  )

  const handleSaveAnnotation = useCallback(async (
    item: AnnotationQueueItem,
    draft: AnnotationSaveDraft,
  ) => {
    await saveModelAnnotation(projectId, item, draft)
    setAnnotationQueue((current) => current.filter((queueItem) => queueItem.id !== item.id))
    setBuildingUnits((current) => mergeManualBuildingUnit(current, draft))
    message.success('人工识别结果已保存')
  }, [projectId])

  const handlePreviewSemanticOperation = useCallback(async (
    draft: SemanticOperationDraft,
  ): Promise<SemanticOperationPreview> => {
    return previewProjectModelSemanticImpact(projectId, semanticOperationPayload(draft))
  }, [projectId])

  const handleSubmitSemanticOperation = useCallback(async (
    draft: SemanticOperationDraft,
  ): Promise<SemanticOperationOutcome> => {
    try {
      await applyProjectModelSemanticOperation(projectId, semanticOperationPayload(draft))
      await fetchModel()
      message.success('语义修正已提交')
      return { ok: true }
    } catch (error) {
      const staleVersion = readErrorNumber(error, 'version', 'expected_version', 'expectedVersion')
      if ((error as RequestLikeError)?.response?.status === 409 && staleVersion) {
        message.warning('语义树版本已更新，请刷新后重试')
        return {
          ok: false,
          staleVersion,
          message: readErrorString(error, 'message') ?? '语义树版本已更新',
        }
      }
      message.error(readErrorString(error, 'message') ?? '语义修正失败')
      return {
        ok: false,
        message: readErrorString(error, 'message') ?? '语义修正失败',
      }
    }
  }, [fetchModel, projectId])

  const handleSelectSemanticNode = useCallback((node: SemanticTreeNodeView | null) => {
    setSelectedSemanticNode(node)
    if (!node) return
    if (node.nodeType === 'building_unit') {
      setSelectedBuildingKey(node.id)
      return
    }
    let currentParentId = node.parentId
    while (currentParentId) {
      const parent = semanticNodeMap[currentParentId]
      if (parent?.nodeType === 'building_unit') {
        setSelectedBuildingKey(currentParentId)
        return
      }
      currentParentId = parent?.parentId ?? null
    }
  }, [semanticNodeMap])

  // 属性面板「在语义树中定位」：按构件名匹配语义节点（Phase A 名称级 best-effort）
  const handleLocateFragmentInTree = useCallback(
    (item: PickedFragmentItem) => {
      const node = findSemanticNodeForItem(semanticTreeGroups, item)
      if (node) handleSelectSemanticNode(node)
    },
    [semanticTreeGroups, handleSelectSemanticNode],
  )

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
              {quality.pendingManualCount > 0 ? (
                <Tag color="gold">待人工识别 {quality.pendingManualCount}</Tag>
              ) : null}
              {scene.stats.reconstruction ? (
                <Tooltip title="构件级重建、贴图级与混合模式保持可用；LOD 入口单独控制审图骨架/建筑体量/实景近似。">
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

        <Space size={8} wrap style={{ marginTop: 12 }}>
          {lodModes.map((item) => {
            const button = (
              <Button
                key={item.key}
                type={lodMode === item.key ? 'primary' : 'default'}
                disabled={!item.enabled}
                onClick={() => setLodMode(item.key)}
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
              onChange={(value) => setViewMode(value as ModelViewMode)}
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

      {scene ? (
        <Row gutter={12} wrap>
          <Col flex="280px">
            {semanticTreeGroups.length > 0 ? (
              <Card size="small" title="语义树" style={{ marginBottom: 12 }}>
                <SemanticTreePanel
                  groups={semanticTreeGroups}
                  selectedNodeId={selectedSemanticNode?.id}
                  onSelectNode={handleSelectSemanticNode}
                />
              </Card>
            ) : null}

            {buildingUnits.length > 0 ? (
              <Card size="small" title="单体" style={{ marginBottom: 12 }}>
                <List
                  size="small"
                  dataSource={buildingUnits}
                  renderItem={(building) => {
                    const isActive = selectedBuildingKey === building.key
                    return (
                      <List.Item
                        onClick={() => {
                          setSelectedBuildingKey(isActive ? null : building.key)
                          setIsolatedFloorKey(null)
                        }}
                        style={{
                          cursor: 'pointer',
                          paddingLeft: 8,
                          paddingRight: 8,
                          background: isActive ? '#e6f4ff' : undefined,
                          borderRadius: 6,
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
                      onClick={() => setIsolatedFloorKey(isActive ? null : floor.key)}
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

          <Col flex="auto">
            <Card size="small" styles={{ body: { padding: 0 } }}>
              <div
                style={{
                  position: 'relative',
                  height: 'calc(100vh - 260px)',
                  minHeight: 520,
                  border: '2px solid #1677ff',
                  borderRadius: 8,
                  overflow: 'hidden',
                  boxShadow: 'inset 0 0 0 1px rgba(22,119,255,0.15)',
                }}
              >
                {viewMode === 'ifc' && fragKey ? (
                  <>
                    <FragmentsScene
                      ref={fragmentsSceneRef}
                      fragKey={fragKey}
                      resolveAssetUrl={resolveAssetUrl}
                      onItemSelected={handleFragmentPick}
                      cameraPoseRef={fragmentsCameraPose}
                      statusLabel={`单体: ${selectedBuilding?.label ?? '总体'}`}
                      markerScene={viewScene ?? undefined}
                    />
                    {fragmentItem || fragmentItemLoading ? (
                      <div
                        style={{
                          position: 'absolute',
                          top: 12,
                          right: 12,
                          width: 300,
                          maxHeight: 'calc(100% - 24px)',
                          overflow: 'auto',
                        }}
                      >
                        <FragmentPropertyPanel
                          item={fragmentItem}
                          loading={fragmentItemLoading}
                          onClear={() => {
                            pickRequestRef.current += 1
                            setFragmentItem(null)
                            void fragmentsSceneRef.current?.clearHighlight().catch(() => {})
                          }}
                          onLocateInTree={handleLocateFragmentInTree}
                        />
                      </div>
                    ) : null}
                  </>
                ) : (
                  <ModelViewer
                    scene={viewScene ?? scene}
                    focusDrawingId={focusDrawingId}
                    disciplineFilter={disciplineFilter}
                    severityFilter={severityFilter}
                    markerTypeFilter={markerTypeFilter}
                    isolatedFloorKey={isolatedFloorKey}
                    renderMode={viewMode === 'ifc' ? 'mixed' : (viewMode as RenderMode)}
                    elementFilter={elementFilter}
                    resolveAssetUrl={resolveAssetUrl}
                    onSelectDrawing={(drawing) => setSelection({ type: 'drawing', drawing })}
                    onSelectMarker={(marker) => setSelection({ type: 'marker', marker })}
                    onSelectElement={(element) => setSelection({ type: 'element', element })}
                    lodMode={lodMode}
                    lodLabel={currentLod.label}
                    buildingLabel={selectedBuilding?.label}
                    pendingAnnotationCount={quality.pendingManualCount}
                  />
                )}
              </div>
            </Card>
            {!selectedBuilding?.hasGeometry && selectedBuilding ? (
              <Alert
                style={{ marginTop: 12 }}
                type="info"
                showIcon
                message={`${selectedBuilding.label} 暂无可展示几何`}
                description="当前保留数据驱动的单体入口，待后端产出该单体楼层/体量后可直接在此页查看。"
              />
            ) : null}
          </Col>

          <Col flex="360px">
            <Card size="small" title="模型质量" style={{ marginBottom: 12 }}>
              <ModelQualityPanel
                quality={quality}
                buildingUnits={buildingUnits}
                selectedScopeQuality={selectedScopeQuality}
              />
            </Card>
            <CollapsiblePanel
              title="语义审查"
              defaultOpen={false}
              style={{ marginBottom: 12 }}
              maxBodyHeight={420}
              extra={quality.pendingCandidateCount > 0 ? <Tag color="gold">{quality.pendingCandidateCount}</Tag> : null}
            >
              <SemanticReviewQueue
                projectId={projectId}
                items={semanticReviewQueue}
                nodeNameById={Object.fromEntries(
                  Object.values(semanticNodeMap).map((node) => [node.id, node.canonicalName]),
                )}
                onSelectNode={(nodeId) => {
                  const node = semanticNodeMap[nodeId] ?? null
                  handleSelectSemanticNode(node)
                }}
                onPreviewOperation={handlePreviewSemanticOperation}
                onSubmitOperation={handleSubmitSemanticOperation}
                onRefreshRequested={refreshSemanticGraph}
              />
            </CollapsiblePanel>
            <CollapsiblePanel
              title="待人工识别"
              defaultOpen={false}
              maxBodyHeight={420}
              extra={quality.pendingManualCount > 0 ? <Tag color="gold">{quality.pendingManualCount}</Tag> : null}
            >
              <DrawingAnnotationQueue
                items={annotationQueue}
                buildingUnits={buildingUnits}
                storyOptionsByBuilding={storyOptionsByBuilding}
                onSave={handleSaveAnnotation}
              />
            </CollapsiblePanel>
            <CollapsiblePanel
              title="楼层标高校正"
              defaultOpen={false}
              maxBodyHeight={460}
              style={{ marginTop: 12 }}
            >
              <StoryHeightPanel projectId={projectId} onSaved={handleRebuild} />
            </CollapsiblePanel>
          </Col>
        </Row>
      ) : (
        <Empty description="模型场景为空，请尝试重建" />
      )}

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

export default function ProjectModelPage() {
  const params = useParams<{ projectId?: string }>()
  const [searchParams] = useSearchParams()
  const focusDrawingId = searchParams.get('focus') ?? undefined

  if (!params.projectId) {
    return <ProjectPicker />
  }
  return <ModelWorkspace projectId={params.projectId} focusDrawingId={focusDrawingId} />
}
