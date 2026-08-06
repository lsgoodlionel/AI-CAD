/**
 * 审校模式右栏面板（D-13/D-14）：审校收件箱（符号+成果审校合并，默认展开）
 * + 语义树候选 + 楼层归属 + 楼层标高校正。四个常驻面板，逐项对应原两个队列的全部动作。
 */
import { Tag } from 'antd'
import HelpTip from '@/components/HelpTip'
import CollapsiblePanel from '../CollapsiblePanel'
import StoryHeightPanel from '../StoryHeightPanel'
import UnifiedReviewInbox from '../review/UnifiedReviewInbox'
import SemanticCandidateQueue from '../review/SemanticCandidateQueue'
import FloorAssignmentQueue from '../review/FloorAssignmentQueue'
import ComponentReviewQueue from '../ComponentReviewQueue'
import ReviewHub from '@/components/ReviewHub'
import type { SymbolDrawingOption } from '../review/reviewInbox'
import type {
  AnnotationQueueItem, AnnotationSaveDraft, BuildingUnitOption,
  SemanticOperationDraft, SemanticOperationOutcome, SemanticOperationPreview,
  SemanticReviewItemView,
} from '../types'

interface ReviewModePanelsProps {
  projectId: string
  symbolDrawings: SymbolDrawingOption[]
  onSelectSemanticNodeById: (nodeId: string) => void
  semanticReviewQueue: SemanticReviewItemView[]
  nodeNameById: Record<string, string>
  onPreviewSemanticOperation: (draft: SemanticOperationDraft) => Promise<SemanticOperationPreview>
  onSubmitSemanticOperation: (draft: SemanticOperationDraft) => Promise<SemanticOperationOutcome>
  onRefreshSemanticGraph: () => Promise<void>
  pendingCandidateCount: number
  annotationQueue: AnnotationQueueItem[]
  buildingUnits: BuildingUnitOption[]
  storyOptionsByBuilding: Record<string, string[]>
  onSaveAnnotation: (item: AnnotationQueueItem, draft: AnnotationSaveDraft) => Promise<void>
  pendingManualCount: number
  onRebuild: () => void
}

export default function ReviewModePanels({
  projectId,
  symbolDrawings,
  onSelectSemanticNodeById,
  semanticReviewQueue,
  nodeNameById,
  onPreviewSemanticOperation,
  onSubmitSemanticOperation,
  onRefreshSemanticGraph,
  pendingCandidateCount,
  annotationQueue,
  buildingUnits,
  storyOptionsByBuilding,
  onSaveAnnotation,
  pendingManualCount,
  onRebuild,
}: ReviewModePanelsProps) {
  return (
    <>
      <ReviewHub projectId={projectId} compact />
      <CollapsiblePanel
        title={<>构件核对<HelpTip content="实体中心装配(Phase H)产出的低置信构件按置信升序排队,显示来源图纸/识别途径,人工确认/否定/改类。人审动作翻转 review_state 并写埋点,驱动模型收敛(auto→confirmed 单调上升)。" anchor="H-构件核对" /></>}
        defaultOpen
        maxBodyHeight={520}
      >
        <ComponentReviewQueue projectId={projectId} />
      </CollapsiblePanel>
      <CollapsiblePanel
        title={<>审校收件箱<HelpTip content="合并符号级候选（置信度+候选框）与成果审校（拓扑闭合/构件命名/规范符合性），按冲突优先、低置信优先排序；支持键盘快捷键流水作业。" anchor="10-审校收件箱" /></>}
        defaultOpen
        maxBodyHeight={520}
      >
        <UnifiedReviewInbox
          projectId={projectId}
          symbolDrawings={symbolDrawings}
          onSelectNode={onSelectSemanticNodeById}
        />
      </CollapsiblePanel>

      <CollapsiblePanel
        title={<>语义树候选<HelpTip content="人工复核单体、分区、功能空间、施工分区等语义候选：确认/拒绝/重命名/合并/拆分/调整父级，提交前可预览影响范围。" anchor="11-语义树候选" /></>}
        defaultOpen={false}
        maxBodyHeight={420}
        extra={pendingCandidateCount > 0 ? <Tag color="gold">{pendingCandidateCount}</Tag> : null}
      >
        <SemanticCandidateQueue
          items={semanticReviewQueue}
          nodeNameById={nodeNameById}
          onSelectNode={onSelectSemanticNodeById}
          onPreviewOperation={onPreviewSemanticOperation}
          onSubmitOperation={onSubmitSemanticOperation}
          onRefreshRequested={onRefreshSemanticGraph}
        />
      </CollapsiblePanel>

      <CollapsiblePanel
        title={<>楼层归属<HelpTip content="AI 无法确定楼层归属的图纸，在此人工补充「单体/楼层/图纸类型」，结果会回流用于下次模型重建。" anchor="12-楼层归属" /></>}
        defaultOpen={false}
        maxBodyHeight={420}
        extra={pendingManualCount > 0 ? <Tag color="gold">{pendingManualCount}</Tag> : null}
      >
        <FloorAssignmentQueue
          items={annotationQueue}
          buildingUnits={buildingUnits}
          storyOptionsByBuilding={storyOptionsByBuilding}
          onSave={onSaveAnnotation}
        />
      </CollapsiblePanel>

      <CollapsiblePanel
        title={<>楼层标高校正<HelpTip content="AI 自动打底的楼层标高/层高不准时，在此人工录入正确值；修正会累加抬升上层楼层，需重建模型后生效。" anchor="" /></>}
        defaultOpen={false}
        maxBodyHeight={460}
      >
        <StoryHeightPanel projectId={projectId} onSaved={onRebuild} />
      </CollapsiblePanel>
    </>
  )
}
