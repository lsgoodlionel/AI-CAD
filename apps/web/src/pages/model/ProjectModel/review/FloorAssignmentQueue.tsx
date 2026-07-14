/**
 * 楼层归属队列（原 DrawingAnnotationQueue.tsx「楼层归属」Tab，D-13 拆分后独立成组件）。
 *
 * 表单式录入（单体/楼层/图纸类型 → 保存），与「审校收件箱」的候选确认/否定语义不同
 * （没有置信度/冲突排序，也没有「否定」动作），因此不纳入 UnifiedReviewInbox 合并，
 * 保留为独立面板。行为与原实现一致：POST /model/annotations（新端点，见 ModelWorkspace
 * saveModelAnnotation），保存成功后从队列移除该项。
 */
import { useEffect, useMemo, useState } from 'react'
import { AutoComplete, Button, Empty, Form, Image, List, Space, Tag, Typography } from 'antd'
import type { AnnotationQueueItem, AnnotationSaveDraft, BuildingUnitOption } from '../types'

const { Text } = Typography

// 每页项数（每项含 3 个 AutoComplete，全量渲染会撑爆 DOM/内存）
const ANNOTATION_PAGE_SIZE = 20

const DRAWING_TYPE_OPTIONS = ['平面图', '立面图', '剖面图', '节点详图', '机电平面']

interface FloorAssignmentQueueProps {
  items: AnnotationQueueItem[]
  buildingUnits: BuildingUnitOption[]
  storyOptionsByBuilding: Record<string, string[]>
  onSave: (item: AnnotationQueueItem, draft: AnnotationSaveDraft) => Promise<void>
}

interface DraftState extends AnnotationSaveDraft {}

function initialDraft(item: AnnotationQueueItem, buildingUnits: BuildingUnitOption[]): DraftState {
  const buildingLabel = item.suggestedBuildingUnitKey
    ? buildingUnits.find((unit) => unit.key === item.suggestedBuildingUnitKey)?.label
    : undefined
  return {
    buildingUnitKey: item.suggestedBuildingUnitKey,
    buildingUnitName: item.suggestedBuildingUnitName ?? buildingLabel ?? '',
    storyKey: item.suggestedStoryKey,
    storyName: item.suggestedStoryName ?? '',
    drawingType: item.suggestedDrawingType ?? '',
  }
}

export default function FloorAssignmentQueue({
  items,
  buildingUnits,
  storyOptionsByBuilding,
  onSave,
}: FloorAssignmentQueueProps) {
  const [drafts, setDrafts] = useState<Record<string, DraftState>>({})
  const [savingId, setSavingId] = useState<string | null>(null)

  useEffect(() => {
    setDrafts((current) => {
      const next: Record<string, DraftState> = {}
      items.forEach((item) => {
        next[item.id] = current[item.id] ?? initialDraft(item, buildingUnits)
      })
      return next
    })
  }, [items, buildingUnits])

  const buildingOptions = useMemo(
    () => buildingUnits.map((unit) => ({ value: unit.label })),
    [buildingUnits],
  )

  if (items.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待人工识别图纸" />
  }

  return (
    <List
      itemLayout="vertical"
      dataSource={items}
      pagination={
        items.length > ANNOTATION_PAGE_SIZE
          ? { pageSize: ANNOTATION_PAGE_SIZE, size: 'small', showSizeChanger: false }
          : false
      }
      renderItem={(item) => {
        const draft = drafts[item.id] ?? initialDraft(item, buildingUnits)
        const matchedUnit = buildingUnits.find((unit) => unit.label === draft.buildingUnitName)
        const storyOptions = (
          matchedUnit ? storyOptionsByBuilding[matchedUnit.key] : storyOptionsByBuilding.__all__
        ) ?? []
        return (
          <List.Item key={item.id}>
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Space align="start" size={12} style={{ width: '100%' }}>
                {item.thumbnailUrl ? (
                  <Image
                    src={item.thumbnailUrl}
                    width={88}
                    height={64}
                    style={{ objectFit: 'cover', borderRadius: 6 }}
                    preview={false}
                    fallback="data:image/gif;base64,R0lGODlhAQABAAAAACw="
                  />
                ) : (
                  <div
                    style={{
                      width: 88, minWidth: 88, height: 64, borderRadius: 6,
                      background: '#fafafa', border: '1px solid #f0f0f0',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      padding: 8, textAlign: 'center',
                    }}
                  >
                    <Text type="secondary">文本线索</Text>
                  </div>
                )}
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text strong>{item.title}</Text>
                  <Text type="secondary">{item.drawingNo}</Text>
                  <Space wrap>
                    {item.clueText.map((clue) => (
                      <Tag key={clue}>{clue}</Tag>
                    ))}
                    {typeof item.confidence === 'number' ? (
                      <Tag color="gold">{Math.round(item.confidence * 100)}%</Tag>
                    ) : null}
                  </Space>
                </Space>
              </Space>

              <Form layout="vertical">
                <Form.Item label="单体" style={{ marginBottom: 10 }}>
                  <AutoComplete
                    options={buildingOptions}
                    value={draft.buildingUnitName}
                    onChange={(value) => {
                      const unit = buildingUnits.find((option) => option.label === value)
                      setDrafts((current) => ({
                        ...current,
                        [item.id]: { ...draft, buildingUnitName: value, buildingUnitKey: unit?.key },
                      }))
                    }}
                    placeholder="输入或选择单体，可新建/重命名"
                    filterOption={(inputValue, option) =>
                      String(option?.value ?? '').toLowerCase().includes(inputValue.toLowerCase())
                    }
                  />
                </Form.Item>
                <Form.Item label="楼层" style={{ marginBottom: 10 }}>
                  <AutoComplete
                    options={storyOptions.map((value) => ({ value }))}
                    value={draft.storyName}
                    onChange={(value) => {
                      setDrafts((current) => ({
                        ...current,
                        [item.id]: { ...draft, storyName: value, storyKey: value || undefined },
                      }))
                    }}
                    placeholder="输入或选择楼层"
                  />
                </Form.Item>
                <Form.Item label="图纸类型" style={{ marginBottom: 10 }}>
                  <AutoComplete
                    options={DRAWING_TYPE_OPTIONS.map((value) => ({ value }))}
                    value={draft.drawingType}
                    onChange={(value) => {
                      setDrafts((current) => ({
                        ...current,
                        [item.id]: { ...draft, drawingType: value },
                      }))
                    }}
                    placeholder="输入或选择图纸类型"
                  />
                </Form.Item>
                <Button
                  type="primary"
                  loading={savingId === item.id}
                  disabled={
                    !draft.buildingUnitName.trim() ||
                    !draft.storyName.trim() ||
                    !draft.drawingType.trim()
                  }
                  onClick={async () => {
                    setSavingId(item.id)
                    try {
                      await onSave(item, draft)
                    } finally {
                      setSavingId((current) => (current === item.id ? null : current))
                    }
                  }}
                >
                  保存标注
                </Button>
              </Form>
            </Space>
          </List.Item>
        )
      }}
    />
  )
}
