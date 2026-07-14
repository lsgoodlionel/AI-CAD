/**
 * 工程模型工作台（D-13 视图模式化）：顶部状态条 + 三视图模式（浏览/审校/算量）+
 * 中央 3D 视图（跨模式常驻挂载，不随模式切换重建）+ 选中详情 Drawer。
 *
 * 状态/数据获取逻辑见 useModelWorkspaceState.ts；面板内容按模式分派到
 * modes/BrowseModePanels · modes/ReviewModePanels · modes/QuantityModePanels。
 */
import { Alert, Button, Col, Descriptions, Drawer, Empty, Row, Space, Spin, Tag, Typography } from 'antd'
import { history } from '@umijs/max'
import ModelHeaderBar from './ModelHeaderBar'
import ModelCanvasArea from './ModelCanvasArea'
import BrowseModePanels from './modes/BrowseModePanels'
import ReviewModePanels from './modes/ReviewModePanels'
import QuantityModePanels from './modes/QuantityModePanels'
import { useModelWorkspaceState } from './useModelWorkspaceState'
import { DISCIPLINE_LABEL, SEVERITY_META, MARKER_TYPE_LABEL } from './modelWorkspaceConstants'
import { ELEMENT_TYPE_LABEL } from './modes/elementFilterOptions'

const { Text } = Typography

interface ModelWorkspaceProps {
  projectId: string
  focusDrawingId?: string
}

export default function ModelWorkspace({ projectId, focusDrawingId }: ModelWorkspaceProps) {
  const state = useModelWorkspaceState(projectId, focusDrawingId)

  if (state.isNotBuilt) {
    return (
      <div style={{ textAlign: 'center', paddingTop: 100 }}>
        <Empty description="该项目尚未生成工程模型">
          <Button type="primary" loading={state.isRebuilding} onClick={state.handleRebuild}>
            立即生成模型
          </Button>
        </Empty>
      </div>
    )
  }

  if (state.isLoading || !state.model) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin tip="加载工程模型…" />
      </div>
    )
  }

  const { scene, selection } = state
  const nodeNameById = Object.fromEntries(
    Object.values(state.semanticNodeMap).map((node) => [node.id, node.canonicalName]),
  )

  return (
    <div style={{ padding: 12 }}>
      <ModelHeaderBar
        mode={state.mode}
        onModeChange={state.setMode}
        model={state.model}
        scene={scene}
        isRebuilding={state.isRebuilding}
        onRebuild={state.handleRebuild}
        pendingManualCount={state.quality.pendingManualCount}
        lodModes={state.lodModes}
        lodMode={state.lodMode}
        onLodModeChange={state.setLodMode}
        viewModeOptions={state.viewModeOptions}
        viewMode={state.viewMode}
        onViewModeChange={state.setViewMode}
      />

      {scene ? (
        <Row gutter={12} wrap>
          <Col key="left" flex={state.mode === 'browse' ? '280px' : '0px'} style={state.mode === 'browse' ? undefined : { display: 'none' }}>
            <BrowseModePanels
              semanticTreeGroups={state.semanticTreeGroups}
              selectedSemanticNode={state.selectedSemanticNode}
              onSelectSemanticNode={state.handleSelectSemanticNode}
              buildingUnits={state.buildingUnits}
              selectedBuildingKey={state.selectedBuildingKey}
              onSelectBuilding={(key) => { state.setSelectedBuildingKey(key); state.setIsolatedFloorKey(null) }}
              sortedFloors={state.sortedFloors}
              isolatedFloorKey={state.isolatedFloorKey}
              onIsolateFloor={state.setIsolatedFloorKey}
              availableDisciplines={state.availableDisciplines}
              disciplineFilter={state.disciplineFilter}
              onDisciplineFilterChange={state.setDisciplineFilter}
              severityFilter={state.severityFilter}
              onSeverityFilterChange={state.setSeverityFilter}
              markerTypeFilter={state.markerTypeFilter}
              onMarkerTypeFilterChange={state.setMarkerTypeFilter}
              isV2={state.isV2}
              viewScene={state.viewScene}
              elementFilter={state.elementFilter}
              onElementFilterChange={state.setElementFilter}
              quality={state.quality}
              selectedScopeQuality={state.selectedScopeQuality}
            />
          </Col>

          <Col key="canvas" flex="auto">
            <ModelCanvasArea
              viewMode={state.viewMode}
              fragKey={state.fragKey}
              scene={scene}
              viewScene={state.viewScene}
              focusDrawingId={state.focusDrawingId}
              disciplineFilter={state.disciplineFilter}
              severityFilter={state.severityFilter}
              markerTypeFilter={state.markerTypeFilter}
              isolatedFloorKey={state.isolatedFloorKey}
              elementFilter={state.elementFilter}
              modelBodyOnly={state.modelBodyOnly}
              onModelBodyOnlyChange={state.setModelBodyOnly}
              resolveAssetUrl={state.resolveAssetUrl}
              onSelect={state.setSelection}
              lodMode={state.lodMode}
              lodLabel={state.currentLod.label}
              pendingAnnotationCount={state.quality.pendingManualCount}
              selectedBuilding={state.selectedBuilding}
              fragmentsSceneRef={state.fragmentsSceneRef}
              fragmentsCameraPose={state.fragmentsCameraPose}
              onFragmentPick={state.handleFragmentPick}
              fragmentItem={state.fragmentItem}
              fragmentItemLoading={state.fragmentItemLoading}
              onClearFragmentSelection={state.handleClearFragmentSelection}
              onLocateFragmentInTree={state.handleLocateFragmentInTree}
            />
          </Col>

          <Col
            key="right"
            flex={state.mode === 'browse' ? '0px' : '360px'}
            style={state.mode === 'browse' ? { display: 'none' } : undefined}
          >
            {state.mode === 'review' ? (
              <ReviewModePanels
                projectId={projectId}
                symbolDrawings={state.symbolDrawingOptions}
                onSelectSemanticNodeById={state.handleSelectSemanticNodeById}
                semanticReviewQueue={state.semanticReviewQueue}
                nodeNameById={nodeNameById}
                onPreviewSemanticOperation={state.handlePreviewSemanticOperation}
                onSubmitSemanticOperation={state.handleSubmitSemanticOperation}
                onRefreshSemanticGraph={state.refreshSemanticGraph}
                pendingCandidateCount={state.quality.pendingCandidateCount}
                annotationQueue={state.annotationQueue}
                buildingUnits={state.buildingUnits}
                storyOptionsByBuilding={state.storyOptionsByBuilding}
                onSaveAnnotation={state.handleSaveAnnotation}
                pendingManualCount={state.quality.pendingManualCount}
                onRebuild={state.handleRebuild}
              />
            ) : null}
            {state.mode === 'quantity' ? (
              <QuantityModePanels
                projectId={projectId}
                isV2={state.isV2}
                viewScene={state.viewScene}
                elementFilter={state.elementFilter}
                onElementFilterChange={state.setElementFilter}
              />
            ) : null}
          </Col>
        </Row>
      ) : (
        <Empty description="模型场景为空，请尝试重建" />
      )}

      <Drawer
        open={selection !== null}
        onClose={() => state.setSelection(null)}
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
              <Descriptions.Item label="当前阶段">{selection.drawing.current_stage}</Descriptions.Item>
              <Descriptions.Item label="问题数">
                {selection.drawing.issue_count}
                {selection.drawing.critical_count > 0 ? (
                  <Tag color="#f5222d" style={{ marginLeft: 8 }}>严重 {selection.drawing.critical_count}</Tag>
                ) : null}
              </Descriptions.Item>
            </Descriptions>
            <Button
              type="primary" block style={{ marginTop: 16 }}
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
                <Descriptions.Item label="专业代码">{selection.marker.discipline_code || '—'}</Descriptions.Item>
                <Descriptions.Item label="所属图纸">
                  {state.markerDrawing
                    ? `${state.markerDrawing.drawing_no} ${state.markerDrawing.title}`
                    : selection.marker.ref.drawing_id || '—'}
                </Descriptions.Item>
              </Descriptions>
            </Space>
            {selection.marker.ref.drawing_id ? (
              <Button
                type="primary" block style={{ marginTop: 16 }}
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
              {selection.element.label ? <Descriptions.Item label="标注">{selection.element.label}</Descriptions.Item> : null}
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
                type="primary" block style={{ marginTop: 16 }}
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
