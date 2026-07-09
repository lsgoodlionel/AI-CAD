import type {
  ProjectModelResponse,
  SceneBuilding,
  SceneFloor,
  SemanticNode,
  SemanticNodeType,
} from '@/services/projectModel'
import type {
  AnnotationQueueItem,
  BuildingUnitOption,
  FloorConflictSummary,
  LodModeOption,
  LowConfidenceBuildingUnit,
  ModelQualitySummary,
  NormalizedModelInsights,
  SemanticReviewItemView,
  SemanticScopeLodView,
  SemanticTreeGroup,
  SemanticTreeNodeView,
} from './types'

type UnknownRecord = Record<string, unknown>

const SEMANTIC_TYPE_LABEL: Record<SemanticNodeType, string> = {
  building_unit: '单体',
  sub_zone: '分区',
  functional_space: '功能空间',
  construction_zone: '施工分区',
}

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : null
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function readString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return undefined
}

function readNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value)
      if (Number.isFinite(parsed)) return parsed
    }
  }
  return undefined
}

function readStringArray(value: unknown): string[] {
  return asArray(value)
    .map((item) => readString(item))
    .filter((item): item is string => Boolean(item))
}

function normalizeSemanticNodeType(value: unknown): SemanticNodeType | null {
  const raw = readString(value)?.toLowerCase()
  if (!raw) return null
  const aliases: Record<string, SemanticNodeType> = {
    building: 'building_unit',
    building_unit: 'building_unit',
    buildingunit: 'building_unit',
    sub_zone: 'sub_zone',
    subzone: 'sub_zone',
    zone: 'sub_zone',
    functional_space: 'functional_space',
    functionalspace: 'functional_space',
    space: 'functional_space',
    room: 'functional_space',
    construction_zone: 'construction_zone',
    constructionzone: 'construction_zone',
    work_zone: 'construction_zone',
  }
  return aliases[raw] ?? null
}

function normalizeKey(input: string): string {
  const ascii = input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return ascii || `manual-${Date.now()}`
}

function unitFromRaw(
  raw: unknown,
  source: BuildingUnitOption['source'],
  geometryKeys: Set<string>,
): BuildingUnitOption | null {
  if (typeof raw === 'string' && raw.trim()) {
    const key = normalizeKey(raw)
    return {
      key,
      label: raw.trim(),
      source,
      hasGeometry: geometryKeys.has(key),
    }
  }
  const record = asRecord(raw)
  if (!record) return null
  const label = readString(
    record.display_name,
    record.displayName,
    record.label,
    record.name,
    record.unit_name,
  )
  const key = readString(
    record.key,
    record.unit_key,
    record.unitKey,
    record.id,
  ) ?? (label ? normalizeKey(label) : undefined)
  if (!key || !label) return null
  return {
    key,
    label,
    source,
    confidence: readNumber(record.confidence),
    hasGeometry: geometryKeys.has(key),
  }
}

function collectBuildingUnits(model: ProjectModelResponse): BuildingUnitOption[] {
  const scene = asRecord(model.scene)
  const sceneBuildings = asArray(scene?.buildings) as SceneBuilding[]
  const geometryKeys = new Set(
    sceneBuildings
      .map((building) => readString(building.key))
      .filter((value): value is string => Boolean(value)),
  )
  const root = asRecord(model as unknown)
  const containers = [
    asRecord(root?.building_units),
    asRecord(root?.buildingUnits),
    asRecord(scene?.building_units),
    asRecord(scene?.buildingUnits),
  ].filter((value): value is UnknownRecord => Boolean(value))

  const units = new Map<string, BuildingUnitOption>()
  sceneBuildings.forEach((building) => {
    const key = readString(building.key)
    const label = readString(building.label, building.key)
    if (!key || !label) return
    units.set(key, {
      key,
      label,
      source: 'scene',
      hasGeometry: true,
    })
  })

  containers.forEach((container) => {
    asArray(container.detected).forEach((raw) => {
      const unit = unitFromRaw(raw, 'detected', geometryKeys)
      if (unit) units.set(unit.key, { ...units.get(unit.key), ...unit })
    })
    asArray(container.manual).forEach((raw) => {
      const unit = unitFromRaw(raw, 'manual', geometryKeys)
      if (unit) units.set(unit.key, { ...units.get(unit.key), ...unit })
    })
    asArray(container.items).forEach((raw) => {
      const unit = unitFromRaw(raw, 'detected', geometryKeys)
      if (unit) units.set(unit.key, { ...units.get(unit.key), ...unit })
    })
  })

  return Array.from(units.values()).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
}

function collectLowConfidenceUnits(value: unknown): LowConfidenceBuildingUnit[] {
  return asArray(value)
    .map((raw) => {
      const record = asRecord(raw)
      if (!record) return null
      const label = readString(
        record.display_name,
        record.displayName,
        record.label,
        record.name,
      )
      const key = readString(record.key, record.unit_key, record.unitKey) ?? label
      if (!key || !label) return null
      return {
        key,
        label,
        confidence: readNumber(record.confidence),
      }
    })
    .filter((item): item is LowConfidenceBuildingUnit => Boolean(item))
}

function collectFloorConflicts(value: unknown): FloorConflictSummary[] {
  return asArray(value)
    .map((raw, index) => {
      const record = asRecord(raw)
      if (!record) return null
      const message = readString(record.message, record.detail, record.reason)
      if (!message) return null
      return {
        id: readString(record.id) ?? `conflict-${index}`,
        buildingUnitKey: readString(
          record.building_unit_key,
          record.buildingUnitKey,
          record.unit_key,
        ),
        storyKey: readString(record.story_key, record.storyKey, record.floor_key),
        message,
        count: readNumber(record.count, record.affected_count),
      }
    })
    .filter((item): item is FloorConflictSummary => Boolean(item))
}

function semanticNodeFromRaw(raw: unknown, treeVersion: number): SemanticNode | null {
  const record = asRecord(raw)
  if (!record) return null
  const id = readString(record.id, record.node_id)
  const nodeType = normalizeSemanticNodeType(record.node_type ?? record.type)
  const canonicalName = readString(
    record.canonical_name,
    record.canonicalName,
    record.title,
    record.label,
    record.name,
  )
  if (!id || !nodeType || !canonicalName) return null
  return {
    id,
    node_type: nodeType,
    canonical_name: canonicalName,
    normalized_key: readString(record.normalized_key, record.normalizedKey) ?? normalizeKey(canonicalName),
    parent_id: readString(record.parent_id, record.parentId) ?? null,
    status: (
      readString(record.status)?.toLowerCase()
      ?? 'candidate'
    ) as SemanticNode['status'],
    confidence: readNumber(record.confidence) ?? 0,
    source: (
      readString(record.source)?.toLowerCase()
      ?? 'automatic'
    ) as SemanticNode['source'],
    version: readNumber(record.version) ?? treeVersion,
  }
}

function semanticTreeData(model: ProjectModelResponse): {
  version: number
  nodes: SemanticNode[]
} {
  const root = asRecord(model as unknown)
  const scene = asRecord(model.scene)
  const rawTree = asRecord(root?.semantic_tree)
    ?? asRecord(root?.semanticTree)
    ?? asRecord(scene?.semantic_tree)
    ?? asRecord(scene?.semanticTree)

  const version = readNumber(rawTree?.version, root?.semantic_tree_version, scene?.semantic_tree_version) ?? 0
  const nodes = asArray(rawTree?.nodes)
    .map((raw) => semanticNodeFromRaw(raw, version))
    .filter((item): item is SemanticNode => Boolean(item))
  return { version, nodes }
}

function semanticNodeViews(model: ProjectModelResponse): {
  semanticTreeVersion: number
  semanticTreeGroups: SemanticTreeGroup[]
  semanticNodeMap: Record<string, SemanticTreeNodeView>
} {
  const { version, nodes } = semanticTreeData(model)
  const nodeMap = new Map(nodes.map((node) => [node.id, node]))
  const viewNodes = nodes.map<SemanticTreeNodeView>((node) => ({
    id: node.id,
    title: node.canonical_name,
    canonicalName: node.canonical_name,
    normalizedKey: node.normalized_key,
    parentId: node.parent_id ?? null,
    parentName: node.parent_id ? nodeMap.get(node.parent_id)?.canonical_name : undefined,
    nodeType: node.node_type,
    status: node.status,
    confidence: node.confidence,
    source: node.source,
    version: node.version,
  }))
  const viewMap = Object.fromEntries(viewNodes.map((node) => [node.id, node]))
  const semanticTreeGroups = (Object.keys(SEMANTIC_TYPE_LABEL) as SemanticNodeType[])
    .map((type) => ({
      type,
      label: SEMANTIC_TYPE_LABEL[type],
      nodes: viewNodes
        .filter((node) => node.nodeType === type)
        .sort((a, b) => a.canonicalName.localeCompare(b.canonicalName, 'zh-CN')),
    }))
    .filter((group) => group.nodes.length > 0)

  return {
    semanticTreeVersion: version,
    semanticTreeGroups,
    semanticNodeMap: viewMap,
  }
}

function semanticReviewQueueFromModel(
  model: ProjectModelResponse,
  semanticTreeVersion: number,
  semanticNodeMap: Record<string, SemanticTreeNodeView>,
): SemanticReviewItemView[] {
  const root = asRecord(model as unknown)
  const scene = asRecord(model.scene)
  const rawQueue = root?.semantic_review_queue
    ?? root?.semanticReviewQueue
    ?? scene?.semantic_review_queue
    ?? scene?.semanticReviewQueue

  const queueSource = Array.isArray(rawQueue)
    ? rawQueue
    : Object.values(semanticNodeMap)
      .filter((node) => node.status === 'candidate')
      .map((node) => ({
        node_id: node.id,
        title: node.canonicalName,
        canonical_name: node.canonicalName,
        node_type: node.nodeType,
        status: node.status,
        current_parent_id: node.parentId,
        version: semanticTreeVersion || node.version,
        confidence: node.confidence,
        evidence: [],
        valid_targets: {
          merge: Object.values(semanticNodeMap)
            .filter((candidate) => candidate.nodeType === node.nodeType && candidate.id !== node.id)
            .map((candidate) => candidate.id),
          reparent: Object.values(semanticNodeMap)
            .filter((candidate) => candidate.id !== node.id)
            .map((candidate) => candidate.id),
        },
      }))

  return queueSource
    .map((raw) => {
      const record = asRecord(raw) as UnknownRecord | null
      if (!record) return null
      const nodeId = readString(record.node_id, record.id)
      const nodeType = normalizeSemanticNodeType(record.node_type ?? record.type)
      const canonicalName = readString(
        record.canonical_name,
        record.canonicalName,
        record.title,
        semanticNodeMap[nodeId ?? '']?.canonicalName,
      )
      if (!nodeId || !nodeType || !canonicalName) return null
      const validTargets = asRecord(record.valid_targets) ?? asRecord(record.validTargets) ?? {}
      return {
        nodeId,
        title: readString(record.title, canonicalName) ?? canonicalName,
        canonicalName,
        nodeType,
        status: (
          readString(record.status)?.toLowerCase()
          ?? semanticNodeMap[nodeId]?.status
          ?? 'candidate'
        ) as SemanticReviewItemView['status'],
        currentParentId: readString(record.current_parent_id, record.parent_id, semanticNodeMap[nodeId]?.parentId) ?? null,
        currentParentName: (() => {
          const parentId = readString(record.current_parent_id, record.parent_id, semanticNodeMap[nodeId]?.parentId)
          return parentId ? semanticNodeMap[parentId]?.canonicalName : undefined
        })(),
        version: readNumber(record.version) ?? semanticTreeVersion,
        confidence: readNumber(record.confidence, semanticNodeMap[nodeId]?.confidence) ?? 0,
        evidence: asArray(record.evidence)
          .map((evidence, index) => {
            const item = asRecord(evidence)
            if (!item) return null
            const label = readString(item.label, item.title, item.name)
            const detail = readString(item.detail, item.description, item.snippet, item.summary)
            if (!label || !detail) return null
            return {
              id: readString(item.id) ?? `${nodeId}-evidence-${index}`,
              label,
              detail,
              score: readNumber(item.score, item.confidence),
              sourceDrawingId: readString(item.source_drawing_id, item.drawing_id),
            }
          })
          .filter((item): item is SemanticReviewItemView['evidence'][number] => Boolean(item)),
        mergeTargets: readStringArray(validTargets.merge),
        reparentTargets: readStringArray(validTargets.reparent),
      }
    })
    .filter((item): item is SemanticReviewItemView => Boolean(item))
}

function lodCapabilityMapFromModel(
  model: ProjectModelResponse,
  semanticNodeMap: Record<string, SemanticTreeNodeView>,
): Record<string, SemanticScopeLodView> {
  const root = asRecord(model as unknown)
  const scene = asRecord(model.scene)
  const raw = asRecord(root?.lod_capabilities)
    ?? asRecord(root?.lodCapabilities)
    ?? asRecord(scene?.lod_capabilities)
    ?? asRecord(scene?.lodCapabilities)
    ?? {}

  return Object.fromEntries(
    Object.entries(raw)
      .map(([scopeId, value]) => {
        const capability = asRecord(value)
        if (!capability) return null
        const availableModes = readStringArray(capability.available_modes)
          .filter((mode): mode is SemanticScopeLodView['availableModes'][number] =>
            ['review_skeleton', 'architectural_massing', 'realistic_proxy'].includes(mode),
          )
        return [
          scopeId,
          {
            scopeId,
            scopeLabel: semanticNodeMap[scopeId]?.canonicalName ?? scopeId,
            level: readNumber(capability.level, capability.lod_level),
            missingEvidence: readStringArray(capability.missing_evidence),
            passedGates: readStringArray(capability.passed_gates),
            degradationReasons: readStringArray(
              capability.degradation_reasons ?? capability.degrade_reasons,
            ),
            fallbackReasons: readStringArray(capability.fallback_reasons),
            availableModes,
          } satisfies SemanticScopeLodView,
        ] as const
      })
      .filter((entry): entry is readonly [string, SemanticScopeLodView] => Boolean(entry)),
  )
}

function qualityFromModel(
  model: ProjectModelResponse,
  queue: AnnotationQueueItem[],
  semanticQueue: SemanticReviewItemView[],
): ModelQualitySummary {
  const root = asRecord(model as unknown)
  const scene = asRecord(model.scene)
  const quality = (
    asRecord(root?.quality)
    ?? asRecord(root?.model_quality)
    ?? asRecord(scene?.quality)
    ?? asRecord(scene?.model_quality)
    ?? {}
  ) as UnknownRecord

  const floorConflicts = collectFloorConflicts(
    quality.floor_conflicts ?? quality.story_conflicts ?? quality.conflicts,
  )
  const lowConfidenceUnits = collectLowConfidenceUnits(
    quality.low_confidence_building_units
    ?? quality.low_confidence_units
    ?? quality.low_confidence_buildings,
  )

  return {
    unassignedStoryCount: readNumber(
      quality.unassigned_story_count,
      quality.unlayered_count,
      quality.unclassified_story_count,
      quality.unknown_story_count,
    ) ?? 0,
    floorConflictCount: readNumber(
      quality.floor_conflict_count,
      quality.story_conflict_count,
    ) ?? floorConflicts.length,
    floorConflicts,
    lowConfidenceUnits,
    pendingManualCount: readNumber(
      quality.pending_manual_count,
      quality.pending_manual_identification_count,
      quality.pending_annotations,
    ) ?? queue.length,
    pendingCandidateCount: readNumber(
      quality.pending_candidate_count,
      quality.pending_semantic_count,
      quality.pending_semantic_candidates,
    ) ?? semanticQueue.length,
    semanticConflictCount: readNumber(
      quality.semantic_conflict_count,
      quality.semantic_conflicts,
    ) ?? 0,
  }
}

function annotationQueueFromModel(model: ProjectModelResponse): AnnotationQueueItem[] {
  const root = asRecord(model as unknown)
  const scene = asRecord(model.scene)
  const queueSource = (
    root?.annotation_queue
    ?? root?.drawing_annotation_queue
    ?? root?.unclassified_drawings
    ?? scene?.annotation_queue
    ?? scene?.drawing_annotation_queue
    ?? []
  )

  return asArray(queueSource)
    .map((raw, index) => {
      const record = asRecord(raw)
      if (!record) return null
      const drawing = asRecord(record.drawing)
      const detected = asRecord(record.detected)
        ?? asRecord(record.suggested)
        ?? asRecord(record.annotation)
        ?? asRecord(record.prediction)
      const drawingId = readString(
        record.drawing_id,
        drawing?.drawing_id,
        record.id,
      )
      const title = readString(record.title, drawing?.title)
      if (!drawingId || !title) return null
      const clues = asArray(record.clue_text ?? record.clues ?? record.text_clues)
        .map((value) => readString(value))
        .filter((value): value is string => Boolean(value))
      return {
        id: readString(record.id) ?? `annotation-${index}`,
        drawingId,
        drawingNo: readString(record.drawing_no, drawing?.drawing_no) ?? drawingId,
        title,
        thumbnailUrl: readString(
          record.thumbnail_url,
          record.thumbnailUrl,
          drawing?.thumbnail_url,
          drawing?.image_url,
        ),
        clueText: clues,
        confidence: readNumber(record.confidence, detected?.confidence),
        suggestedBuildingUnitKey: readString(
          record.building_unit_key,
          detected?.building_unit_key,
          detected?.unit_key,
        ),
        suggestedBuildingUnitName: readString(
          record.building_unit_name,
          detected?.building_unit_name,
          detected?.display_name,
          detected?.label,
        ),
        suggestedStoryKey: readString(
          record.story_key,
          detected?.story_key,
          detected?.floor_key,
        ),
        suggestedStoryName: readString(
          record.story_name,
          detected?.story_name,
          detected?.floor_label,
        ),
        suggestedDrawingType: readString(
          record.drawing_type,
          detected?.drawing_type,
          record.type,
        ),
      }
    })
    .filter((item): item is AnnotationQueueItem => Boolean(item))
}

function lodModesFromModel(model: ProjectModelResponse): LodModeOption[] {
  const root = asRecord(model as unknown)
  const scene = asRecord(model.scene)
  const raw = asRecord(root?.lod_modes) ?? asRecord(root?.lodModes) ?? asRecord(scene?.lod_modes)
  const capabilityMap = lodCapabilityMapFromModel(model, semanticNodeViews(model).semanticNodeMap)
  const capabilityEntries = Object.values(capabilityMap)
  const capabilityReasons = capabilityEntries.flatMap((item) => [
    ...item.degradationReasons,
    ...item.fallbackReasons,
  ])

  const defaults: LodModeOption[] = [
    { key: 'review_skeleton', label: '审图骨架', enabled: true },
    { key: 'architectural_massing', label: '建筑体量', enabled: true },
    { key: 'realistic_proxy', label: '实景近似', enabled: true },
  ]

  const inferredModeSupport = {
    review_skeleton: capabilityEntries.length > 0,
    architectural_massing: capabilityEntries.length > 0,
    realistic_proxy: capabilityEntries.some((item) =>
      item.availableModes.includes('realistic_proxy') || (item.level ?? 0) >= 200,
    ),
  }

  if (!raw) {
    return defaults.map((item) =>
      item.key === 'realistic_proxy' && inferredModeSupport.realistic_proxy
        ? {
          ...item,
          enabled: true,
          reason: capabilityReasons[0],
        }
        : item,
    )
  }

  return defaults.map((item) => {
    const record = asRecord(raw[item.key]) ?? asRecord(raw[item.key.replace(/_[a-z]/g, (match) => match[1].toUpperCase())])
    const enabled = typeof record?.enabled === 'boolean'
      ? record.enabled
      : inferredModeSupport[item.key] || item.enabled
    return {
      key: item.key,
      label: readString(record?.label, record?.display_name) ?? item.label,
      enabled,
      reason: readString(record?.reason, record?.disabled_reason) ?? capabilityReasons[0] ?? item.reason,
    }
  })
}

export function normalizeModelInsights(model: ProjectModelResponse): NormalizedModelInsights {
  const annotationQueue = annotationQueueFromModel(model)
  const semanticTree = semanticNodeViews(model)
  const semanticReviewQueue = semanticReviewQueueFromModel(
    model,
    semanticTree.semanticTreeVersion,
    semanticTree.semanticNodeMap,
  )
  return {
    buildingUnits: collectBuildingUnits(model),
    quality: qualityFromModel(model, annotationQueue, semanticReviewQueue),
    annotationQueue,
    lodModes: lodModesFromModel(model),
    semanticTreeVersion: semanticTree.semanticTreeVersion,
    semanticTreeGroups: semanticTree.semanticTreeGroups,
    semanticNodeMap: semanticTree.semanticNodeMap,
    semanticReviewQueue,
    lodCapabilityMap: lodCapabilityMapFromModel(model, semanticTree.semanticNodeMap),
  }
}

export function resolveScopeLodQuality(
  insights: NormalizedModelInsights,
  scopeId?: string | null,
): SemanticScopeLodView | null {
  if (!scopeId) {
    return Object.values(insights.lodCapabilityMap)[0] ?? null
  }

  let currentId: string | null | undefined = scopeId
  while (currentId) {
    const direct = insights.lodCapabilityMap[currentId]
    if (direct) return direct
    currentId = insights.semanticNodeMap[currentId]?.parentId
  }

  return Object.values(insights.lodCapabilityMap)[0] ?? null
}

export function buildStoryOptions(
  scene: ProjectModelResponse['scene'],
  buildingUnits: BuildingUnitOption[],
): Record<string, string[]> {
  const result: Record<string, string[]> = {}
  if (!scene) return result
  const sceneBuildings = asArray((scene as unknown as UnknownRecord).buildings) as SceneBuilding[]
  const sceneFloors = scene.floors as SceneFloor[]

  const allFloors = sceneFloors
    .map((floor) => readString(floor.label, floor.key))
    .filter((value): value is string => Boolean(value))

  buildingUnits.forEach((unit) => {
    const building = sceneBuildings.find((item) => item.key === unit.key)
    const floors = (building?.floors ?? sceneFloors)
      .map((floor) => readString(floor.label, floor.key))
      .filter((value): value is string => Boolean(value))
    result[unit.key] = Array.from(new Set(floors.length ? floors : allFloors))
  })

  result.__all__ = Array.from(new Set(allFloors))
  return result
}
