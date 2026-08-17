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

/**
 * 层内坐标系矛盾 —— 该层既有绝对摆放的图、又有只能相对配准的图。
 *
 * **不能自动统一到世界坐标**：工程坐标与图纸有 70.29° 旋转，而 scene 的
 * `axes` 是轴对齐结构装不下斜轴线。所以矛盾层整层退回局部，
 * 并把判断依据交给人（用户口径：矛盾时出矛盾点，提交人工判断）。
 */
export interface CoordinateConflict {
  floor: string
  /** 解出世界锚点的图数 */
  placedCount: number
  /** 只能相对配准的图数 */
  unplacedCount: number
  /** 两组构件中心相距多远（米）—— 千米量级才是真跨坐标系 */
  distanceM?: number
  /** 人话版：为什么算矛盾、两条处置路径分别意味着什么 */
  explanation: string
  /** 系统已经做了什么（降级必须可见） */
  resolution: string
}

export interface ModelQualitySummary {
  /** 坐标系矛盾的层 —— 它们的构件已退回局部，世界坐标暂不生效 */
  coordinateConflicts: CoordinateConflict[]
  unassignedStoryCount: number
  floorConflictCount: number
  floorConflicts: FloorConflictSummary[]
  unregisteredFloorCount: number
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
