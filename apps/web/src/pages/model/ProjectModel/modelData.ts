import type { ProjectModelResponse, SceneBuilding, SceneFloor } from '@/services/projectModel'
import type {
  AnnotationQueueItem,
  BuildingUnitOption,
  FloorConflictSummary,
  LodModeOption,
  LowConfidenceBuildingUnit,
  ModelQualitySummary,
  NormalizedModelInsights,
} from './types'

type UnknownRecord = Record<string, unknown>

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

function qualityFromModel(
  model: ProjectModelResponse,
  queue: AnnotationQueueItem[],
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

  const defaults: LodModeOption[] = [
    { key: 'review_skeleton', label: '审图骨架', enabled: true },
    { key: 'architectural_massing', label: '建筑体量', enabled: true },
    { key: 'realistic_proxy', label: '实景近似', enabled: false, reason: '需要 LOD300 数据' },
  ]

  if (!raw) return defaults

  return defaults.map((item) => {
    const record = asRecord(raw[item.key]) ?? asRecord(raw[item.key.replace(/_[a-z]/g, (match) => match[1].toUpperCase())])
    if (!record) return item
    const enabled = typeof record.enabled === 'boolean' ? record.enabled : item.enabled
    return {
      key: item.key,
      label: readString(record.label, record.display_name) ?? item.label,
      enabled,
      reason: readString(record.reason, record.disabled_reason) ?? item.reason,
    }
  })
}

export function normalizeModelInsights(model: ProjectModelResponse): NormalizedModelInsights {
  const annotationQueue = annotationQueueFromModel(model)
  return {
    buildingUnits: collectBuildingUnits(model),
    quality: qualityFromModel(model, annotationQueue),
    annotationQueue,
    lodModes: lodModesFromModel(model),
  }
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
