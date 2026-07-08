export type ModelLodMode =
  | 'review_skeleton'
  | 'architectural_massing'
  | 'realistic_proxy'

export interface BuildingUnitOption {
  key: string
  label: string
  source: 'detected' | 'manual' | 'scene' | 'unknown'
  confidence?: number
  hasGeometry?: boolean
}

export interface FloorConflictSummary {
  id: string
  buildingUnitKey?: string
  storyKey?: string
  message: string
  count?: number
}

export interface LowConfidenceBuildingUnit {
  key: string
  label: string
  confidence?: number
}

export interface ModelQualitySummary {
  unassignedStoryCount: number
  floorConflictCount: number
  floorConflicts: FloorConflictSummary[]
  lowConfidenceUnits: LowConfidenceBuildingUnit[]
  pendingManualCount: number
}

export interface AnnotationQueueItem {
  id: string
  drawingId: string
  drawingNo: string
  title: string
  thumbnailUrl?: string
  clueText: string[]
  confidence?: number
  suggestedBuildingUnitKey?: string
  suggestedBuildingUnitName?: string
  suggestedStoryKey?: string
  suggestedStoryName?: string
  suggestedDrawingType?: string
}

export interface AnnotationSaveDraft {
  buildingUnitKey?: string
  buildingUnitName: string
  storyKey?: string
  storyName: string
  drawingType: string
}

export interface LodModeOption {
  key: ModelLodMode
  label: string
  enabled: boolean
  reason?: string
}

export interface NormalizedModelInsights {
  buildingUnits: BuildingUnitOption[]
  quality: ModelQualitySummary
  annotationQueue: AnnotationQueueItem[]
  lodModes: LodModeOption[]
}
