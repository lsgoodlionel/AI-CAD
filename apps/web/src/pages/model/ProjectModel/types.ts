import type {
  SemanticNodeSource,
  SemanticNodeStatus,
  SemanticNodeType,
  SemanticOperationImpact,
  SemanticOperationType,
} from '@/services/projectModel'

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
  pendingCandidateCount: number
  semanticConflictCount: number
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

export interface SemanticTreeNodeView {
  id: string
  title: string
  canonicalName: string
  normalizedKey: string
  parentId?: string | null
  parentName?: string
  nodeType: SemanticNodeType
  status: SemanticNodeStatus
  confidence: number
  source: SemanticNodeSource
  version: number
}

export interface SemanticTreeGroup {
  type: SemanticNodeType
  label: string
  nodes: SemanticTreeNodeView[]
}

export interface SemanticEvidenceView {
  id: string
  label: string
  detail: string
  score?: number
  sourceDrawingId?: string
}

export interface SemanticReviewItemView {
  nodeId: string
  title: string
  canonicalName: string
  nodeType: SemanticNodeType
  status: SemanticNodeStatus
  currentParentId?: string | null
  currentParentName?: string
  version: number
  confidence: number
  evidence: SemanticEvidenceView[]
  mergeTargets: string[]
  reparentTargets: string[]
}

export interface SemanticScopeLodView {
  scopeId: string
  scopeLabel: string
  level?: number
  missingEvidence: string[]
  passedGates: string[]
  degradationReasons: string[]
  fallbackReasons: string[]
  availableModes: ModelLodMode[]
}

export interface SemanticOperationDraft {
  operation: SemanticOperationType
  nodeId: string
  version: number
  targetNodeId?: string
  newName?: string
  splitNames?: string[]
}

export interface SemanticOperationOutcome {
  ok: boolean
  staleVersion?: number
  message?: string
}

export interface SemanticOperationPreview extends SemanticOperationImpact {}

export interface NormalizedModelInsights {
  buildingUnits: BuildingUnitOption[]
  quality: ModelQualitySummary
  annotationQueue: AnnotationQueueItem[]
  lodModes: LodModeOption[]
  semanticTreeVersion: number
  semanticTreeGroups: SemanticTreeGroup[]
  semanticNodeMap: Record<string, SemanticTreeNodeView>
  semanticReviewQueue: SemanticReviewItemView[]
  lodCapabilityMap: Record<string, SemanticScopeLodView>
}
