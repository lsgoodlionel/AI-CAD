/**
 * 中央 3D 视图区（IFC/Fragments 或 ModelViewer 构件/贴图/混合），从原 index.tsx 迁出。
 * 在浏览/审校/算量三模式切换时保持挂载在同一位置，避免 three.js/Fragments 场景反复重建。
 */
import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from 'react'
import { Alert, Button, Card, Tooltip } from 'antd'
import type { ModelScene, PickedFragmentItem, SceneDrawing, SceneMarker } from '@/services/projectModel'
import ModelViewer from './ModelViewer'
import type { RenderMode } from './ModelViewer'
import FragmentsScene from './FragmentsScene'
import type { FragmentPickResult, FragmentsCameraPose, FragmentsSceneHandle } from './FragmentsScene'
import FragmentPropertyPanel from './FragmentPropertyPanel'
import type { ElementUserData } from './elementsBuilder'
import type { ModelViewMode } from './sceneBuilder'
import type { BuildingUnitOption, ModelLodMode } from './types'
import type { Selection } from './useModelWorkspaceState'

interface ModelCanvasAreaProps {
  viewMode: ModelViewMode
  fragKey: string | null
  scene: ModelScene
  viewScene: ModelScene | null
  focusDrawingId?: string
  disciplineFilter: string[]
  severityFilter: string[]
  markerTypeFilter: string[]
  isolatedFloorKey: string | null
  elementFilter: string[] | undefined
  showFloorBoards: boolean
  onShowFloorBoardsChange: (visible: boolean) => void
  resolveAssetUrl: (key: string) => Promise<string>
  onSelect: Dispatch<SetStateAction<Selection | null>>
  lodMode: ModelLodMode
  lodLabel: string
  pendingAnnotationCount: number
  selectedBuilding: BuildingUnitOption | null
  fragmentsSceneRef: RefObject<FragmentsSceneHandle>
  fragmentsCameraPose: MutableRefObject<FragmentsCameraPose | null>
  onFragmentPick: (pick: FragmentPickResult | null) => void
  fragmentItem: PickedFragmentItem | null
  fragmentItemLoading: boolean
  onClearFragmentSelection: () => void
  onLocateFragmentInTree: (item: PickedFragmentItem) => void
}

export default function ModelCanvasArea({
  viewMode,
  fragKey,
  scene,
  viewScene,
  focusDrawingId,
  disciplineFilter,
  severityFilter,
  markerTypeFilter,
  isolatedFloorKey,
  elementFilter,
  showFloorBoards,
  onShowFloorBoardsChange,
  resolveAssetUrl,
  onSelect,
  lodMode,
  lodLabel,
  pendingAnnotationCount,
  selectedBuilding,
  fragmentsSceneRef,
  fragmentsCameraPose,
  onFragmentPick,
  fragmentItem,
  fragmentItemLoading,
  onClearFragmentSelection,
  onLocateFragmentInTree,
}: ModelCanvasAreaProps) {
  return (
    <>
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
                onItemSelected={onFragmentPick}
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
                    onClear={onClearFragmentSelection}
                    onLocateInTree={onLocateFragmentInTree}
                  />
                </div>
              ) : null}
            </>
          ) : (
            <>
              <ModelViewer
                scene={viewScene ?? scene}
                focusDrawingId={focusDrawingId}
                disciplineFilter={disciplineFilter}
                severityFilter={severityFilter}
                markerTypeFilter={markerTypeFilter}
                isolatedFloorKey={isolatedFloorKey}
                renderMode={viewMode === 'ifc' ? 'mixed' : (viewMode as RenderMode)}
                elementFilter={elementFilter}
                showFloorBoards={showFloorBoards}
                resolveAssetUrl={resolveAssetUrl}
                onSelectDrawing={(drawing: SceneDrawing) => onSelect({ type: 'drawing', drawing })}
                onSelectMarker={(marker: SceneMarker) => onSelect({ type: 'marker', marker })}
                onSelectElement={(element: ElementUserData) => onSelect({ type: 'element', element })}
                lodMode={lodMode}
                lodLabel={lodLabel}
                buildingLabel={selectedBuilding?.label}
                pendingAnnotationCount={pendingAnnotationCount}
              />
              <Tooltip title="楼层板片为蓝色半透明堆叠层，示意楼层分布；隐藏后仅保留构件/贴图">
                <Button
                  size="small"
                  type={showFloorBoards ? 'default' : 'primary'}
                  onClick={() => onShowFloorBoardsChange(!showFloorBoards)}
                  style={{ position: 'absolute', top: 12, right: 12 }}
                >
                  {showFloorBoards ? '隐藏楼层板片' : '显示楼层板片'}
                </Button>
              </Tooltip>
            </>
          )}
        </div>
      </Card>
      {selectedBuilding && !selectedBuilding.hasGeometry ? (
        <Alert
          style={{ marginTop: 12 }}
          type="info"
          showIcon
          message={`${selectedBuilding.label} 暂无可展示几何`}
          description="当前保留数据驱动的单体入口，待后端产出该单体楼层/体量后可直接在此页查看。"
        />
      ) : null}
    </>
  )
}
