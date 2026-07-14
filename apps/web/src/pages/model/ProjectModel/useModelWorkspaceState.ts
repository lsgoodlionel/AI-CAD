/**
 * 工程模型工作台状态钩子：数据获取/轮询/筛选/选中态/浏览-审校-算量三模式切换。
 * 从原 index.tsx 的 ModelWorkspace 组件体拆出（保留全部行为），ModelWorkspace.tsx 只负责布局。
 * 构件拾取（useFragmentSelection）与语义操作（useSemanticOperations）进一步拆到独立钩子。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { message } from 'antd'
import {
  getModelAssetUrl,
  getProjectModel,
  getProjectModelSemanticGraph,
  rebuildProjectModel,
} from '@/services/projectModel'
import type {
  ModelScene,
  PickedFragmentItem,
  ProjectModelResponse,
  SceneDrawing,
  SceneMarker,
} from '@/services/projectModel'
import type { ElementUserData } from './elementsBuilder'
import type { ModelViewMode } from './sceneBuilder'
import { pickDefaultViewMode, readModelIfc } from './sceneBuilder'
import { buildStoryOptions, normalizeModelInsights, resolveScopeLodQuality } from './modelData'
import type {
  AnnotationQueueItem,
  AnnotationSaveDraft,
  BuildingUnitOption,
  ModelLodMode,
  SemanticTreeNodeView,
} from './types'
import type { SymbolDrawingOption } from './review/reviewInbox'
import { findSemanticNodeForItem } from './fragmentsPicking'
import { activeLodMode, isNotBuiltError, mergeManualBuildingUnit, saveModelAnnotation } from './workspaceHelpers'
import { ALL_MARKER_TYPES, ALL_SEVERITIES, EMPTY_QUALITY, POLL_INTERVAL_MS } from './modelWorkspaceConstants'
import { useFragmentSelection } from './useFragmentSelection'
import { useSemanticOperations } from './useSemanticOperations'

export type WorkspaceMode = 'browse' | 'review' | 'quantity'

export type Selection =
  | { type: 'drawing'; drawing: SceneDrawing }
  | { type: 'marker'; marker: SceneMarker }
  | { type: 'element'; element: ElementUserData }

export function useModelWorkspaceState(projectId: string, focusDrawingId?: string) {
  const [mode, setMode] = useState<WorkspaceMode>('browse')
  const [model, setModel] = useState<ProjectModelResponse | null>(null)
  const [isNotBuilt, setIsNotBuilt] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [isRebuilding, setIsRebuilding] = useState(false)
  const [disciplineFilter, setDisciplineFilter] = useState<string[]>([])
  const [severityFilter, setSeverityFilter] = useState<string[]>(ALL_SEVERITIES)
  const [markerTypeFilter, setMarkerTypeFilter] = useState<string[]>(ALL_MARKER_TYPES)
  const [isolatedFloorKey, setIsolatedFloorKey] = useState<string | null>(null)
  const [showFloorBoards, setShowFloorBoards] = useState(true)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [viewMode, setViewMode] = useState<ModelViewMode>('mixed')
  const [elementFilter, setElementFilter] = useState<string[] | undefined>(undefined)
  const [selectedBuildingKey, setSelectedBuildingKey] = useState<string | null>(null)
  const [selectedSemanticNode, setSelectedSemanticNode] = useState<SemanticTreeNodeView | null>(null)
  const [buildingUnits, setBuildingUnits] = useState<BuildingUnitOption[]>([])
  const [annotationQueue, setAnnotationQueue] = useState<AnnotationQueueItem[]>([])
  const [lodMode, setLodMode] = useState<ModelLodMode>('review_skeleton')

  const fragmentSelection = useFragmentSelection(viewMode)

  const scene: ModelScene | null = model?.scene ?? null
  const isV2 = scene?.schema_version === 2
  const modelIfc = useMemo(() => (scene ? readModelIfc(scene) : null), [scene])
  const fragKey = modelIfc?.frag_key ?? null
  const insights = useMemo(() => (model ? normalizeModelInsights(model) : null), [model])
  const lodModes = insights?.lodModes ?? [
    { key: 'review_skeleton' as ModelLodMode, label: '审图骨架', enabled: true },
    { key: 'architectural_massing' as ModelLodMode, label: '建筑体量', enabled: true },
    { key: 'realistic_proxy' as ModelLodMode, label: '实景近似', enabled: true },
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

  const quality = useMemo(() => {
    if (!insights) return EMPTY_QUALITY
    return {
      ...insights.quality,
      pendingManualCount: annotationQueue.length,
    }
  }, [insights, annotationQueue.length])
  const selectedScopeQuality = useMemo(
    () => (
      insights
        ? resolveScopeLodQuality(insights, selectedSemanticNode?.id ?? selectedBuildingKey)
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

  const semanticOperations = useSemanticOperations(projectId, fetchModel)

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

  const handleRebuild = useCallback(async () => {
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
  }, [projectId, fetchModel])

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

  /** 符号标注可选图纸：沿用原行为，取「待人工识别」队列图纸的缩略图（无显式全量图纸缩略图源）。 */
  const symbolDrawingOptions = useMemo<SymbolDrawingOption[]>(
    () => annotationQueue.map((item) => ({
      drawingId: item.drawingId,
      title: `${item.drawingNo} ${item.title}`,
      thumbnailUrl: item.thumbnailUrl,
    })),
    [annotationQueue],
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

  /** 语义树按节点 id 定位（供审校收件箱 semantic 候选确认时回传 drawing_id 时联动选中）。 */
  const handleSelectSemanticNodeById = useCallback((nodeId: string) => {
    const node = semanticNodeMap[nodeId] ?? null
    handleSelectSemanticNode(node)
  }, [semanticNodeMap, handleSelectSemanticNode])

  // 属性面板「在语义树中定位」：按构件名匹配语义节点（Phase A 名称级 best-effort）
  const handleLocateFragmentInTree = useCallback(
    (item: PickedFragmentItem) => {
      const node = findSemanticNodeForItem(semanticTreeGroups, item)
      if (node) handleSelectSemanticNode(node)
    },
    [semanticTreeGroups, handleSelectSemanticNode],
  )

  return {
    projectId,
    focusDrawingId,
    mode,
    setMode,
    model,
    isNotBuilt,
    isLoading,
    isRebuilding,
    disciplineFilter,
    setDisciplineFilter,
    severityFilter,
    setSeverityFilter,
    markerTypeFilter,
    setMarkerTypeFilter,
    isolatedFloorKey,
    setIsolatedFloorKey,
    showFloorBoards,
    setShowFloorBoards,
    selection,
    setSelection,
    viewMode,
    setViewMode,
    ...fragmentSelection,
    elementFilter,
    setElementFilter,
    selectedBuildingKey,
    setSelectedBuildingKey,
    selectedSemanticNode,
    buildingUnits,
    annotationQueue,
    lodMode,
    setLodMode,
    scene,
    isV2,
    fragKey,
    lodModes,
    currentLod,
    semanticTreeGroups,
    semanticNodeMap,
    semanticReviewQueue,
    viewModeOptions,
    availableDisciplines,
    selectedBuilding,
    viewScene,
    storyOptionsByBuilding,
    quality,
    selectedScopeQuality,
    handleRebuild,
    resolveAssetUrl,
    markerDrawing,
    sortedFloors,
    symbolDrawingOptions,
    handleSaveAnnotation,
    ...semanticOperations,
    handleSelectSemanticNode,
    handleSelectSemanticNodeById,
    handleLocateFragmentInTree,
    refreshSemanticGraph,
  }
}

export type ModelWorkspaceState = ReturnType<typeof useModelWorkspaceState>
