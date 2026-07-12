import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from '@umijs/max'
import {
  Alert,
  AutoComplete,
  Button,
  Empty,
  Form,
  Image,
  InputNumber,
  List,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import type {
  AnnotationQueueItem,
  AnnotationSaveDraft,
  BuildingUnitOption,
} from './types'
import type { SymbolAnnotation, SymbolCategory } from '@/services/modelReview'
import {
  confidenceColor,
  listSymbolAnnotations,
  saveSymbolAnnotation,
} from '@/services/modelReview'

const { Text } = Typography

// 待人工识别队列每页项数（每项含 3 个 AutoComplete，全量渲染会撑爆 DOM/内存）
const ANNOTATION_PAGE_SIZE = 20

const DRAWING_TYPE_OPTIONS = ['平面图', '立面图', '剖面图', '节点详图', '机电平面']

// 9 类 taxonomy（对齐后端 layer_conventions / 迁移 024）
const SYMBOL_CATEGORIES: SymbolCategory[] = [
  'column', 'beam', 'slab', 'wall', 'door', 'window', 'pipe', 'equipment', 'axis',
]
const CATEGORY_LABEL: Record<string, string> = {
  column: '柱', beam: '梁', slab: '板', wall: '墙', door: '门',
  window: '窗', pipe: '管线', equipment: '设备', axis: '轴网',
}
const STATUS_LABEL: Record<string, string> = {
  pending: '待审', confirmed: '已确认', rejected: '已否定', reclassed: '已改类',
}
const CATEGORY_OPTIONS = SYMBOL_CATEGORIES.map((value) => ({
  value,
  label: `${CATEGORY_LABEL[value]}（${value}）`,
}))

interface SymbolDrawingOption {
  drawingId: string
  title: string
  thumbnailUrl?: string
}

interface DrawingAnnotationQueueProps {
  items: AnnotationQueueItem[]
  buildingUnits: BuildingUnitOption[]
  storyOptionsByBuilding: Record<string, string[]>
  onSave: (item: AnnotationQueueItem, draft: AnnotationSaveDraft) => Promise<void>
  /** 符号标注区所属项目 id（缺省时从路由 /model/:projectId 推断） */
  projectId?: string
  /** 可做符号标注的图纸（缺省复用楼层队列 items） */
  symbolDrawings?: SymbolDrawingOption[]
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

// ── 楼层归属队列（既有能力，保留）──────────────────────────────

function FloorAssignmentQueue({
  items,
  buildingUnits,
  storyOptionsByBuilding,
  onSave,
}: Pick<DrawingAnnotationQueueProps, 'items' | 'buildingUnits' | 'storyOptionsByBuilding' | 'onSave'>) {
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
      // 分页仅渲染当前页(每项含 3 个重表单控件)，避免上千项一次性挂满 DOM
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
                      width: 88,
                      minWidth: 88,
                      height: 64,
                      borderRadius: 6,
                      background: '#fafafa',
                      border: '1px solid #f0f0f0',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      padding: 8,
                      textAlign: 'center',
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

// ── 符号级标注工作台（C-16：候选框 + 置信度 + 确认/否定/改类/补框）────────

interface RenderSize {
  scaleX: number
  scaleY: number
}

const DEFAULT_BBOX: [number, number, number, number] = [0, 0, 100, 100]

function ConfidenceDot({ confidence }: { confidence?: number | null }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        background: confidenceColor(confidence ?? undefined),
      }}
    />
  )
}

function SymbolAnnotationBoard({
  projectId,
  drawings,
}: {
  projectId?: string
  drawings: SymbolDrawingOption[]
}) {
  const [selectedDrawingId, setSelectedDrawingId] = useState<string | undefined>(
    drawings[0]?.drawingId,
  )
  const [annotations, setAnnotations] = useState<SymbolAnnotation[]>([])
  const [loading, setLoading] = useState(false)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [activeId, setActiveId] = useState<number | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [newCategory, setNewCategory] = useState<SymbolCategory>('column')
  const [newBbox, setNewBbox] = useState<[number, number, number, number]>(DEFAULT_BBOX)
  const [renderSize, setRenderSize] = useState<RenderSize>({ scaleX: 1, scaleY: 1 })
  const imgRef = useRef<HTMLImageElement | null>(null)

  const selectedDrawing = drawings.find((item) => item.drawingId === selectedDrawingId)

  // 低置信优先排序（后端已排序，前端再兜底一次以稳定展示顺序）
  const sortedAnnotations = useMemo(
    () =>
      [...annotations].sort((a, b) => {
        const ca = a.confidence ?? Number.POSITIVE_INFINITY
        const cb = b.confidence ?? Number.POSITIVE_INFINITY
        return ca - cb
      }),
    [annotations],
  )

  const loadAnnotations = useCallback(async () => {
    if (!projectId || !selectedDrawingId) {
      setAnnotations([])
      return
    }
    setLoading(true)
    try {
      const resp = await listSymbolAnnotations(projectId, selectedDrawingId)
      setAnnotations(Array.isArray(resp?.data) ? resp.data : [])
    } catch (error) {
      message.error('加载符号标注失败')
      setAnnotations([])
    } finally {
      setLoading(false)
    }
  }, [projectId, selectedDrawingId])

  useEffect(() => {
    loadAnnotations()
  }, [loadAnnotations])

  const measureImage = useCallback(() => {
    const el = imgRef.current
    if (!el || !el.naturalWidth || !el.naturalHeight) return
    setRenderSize({
      scaleX: el.clientWidth / el.naturalWidth,
      scaleY: el.clientHeight / el.naturalHeight,
    })
  }, [])

  const submitAction = useCallback(
    async (
      actionType: 'confirm' | 'reject' | 'reclass' | 'addbox' | 'edit',
      payload: Partial<SymbolAnnotation>,
      key: string,
    ) => {
      if (!projectId || !selectedDrawingId) {
        message.warning('无法确定项目或图纸')
        return
      }
      setSavingKey(key)
      try {
        await saveSymbolAnnotation(projectId, selectedDrawingId, { ...payload, actionType })
        message.success('已保存并记录人审')
        await loadAnnotations()
      } catch (error) {
        message.error('保存失败')
      } finally {
        setSavingKey((current) => (current === key ? null : current))
      }
    },
    [projectId, selectedDrawingId, loadAnnotations],
  )

  if (!projectId) {
    return <Alert type="warning" showIcon message="无法确定项目 id，符号标注不可用" />
  }
  if (drawings.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可标注图纸" />
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Select
        style={{ width: '100%' }}
        value={selectedDrawingId}
        onChange={(value) => {
          setSelectedDrawingId(value)
          setActiveId(null)
          setShowAddForm(false)
        }}
        options={drawings.map((item) => ({ value: item.drawingId, label: item.title }))}
        placeholder="选择图纸"
      />

      {/* 图纸 + 候选框叠加 */}
      <div style={{ position: 'relative', width: '100%', border: '1px solid #f0f0f0', borderRadius: 6, overflow: 'hidden', minHeight: 120 }}>
        {selectedDrawing?.thumbnailUrl ? (
          <img
            ref={imgRef}
            src={selectedDrawing.thumbnailUrl}
            alt={selectedDrawing.title}
            style={{ display: 'block', width: '100%', height: 'auto' }}
            onLoad={measureImage}
          />
        ) : (
          <div style={{ padding: 24, textAlign: 'center' }}>
            <Text type="secondary">该图纸无预览图，可在下方列表直接审校</Text>
          </div>
        )}
        {selectedDrawing?.thumbnailUrl
          ? sortedAnnotations.map((annotation) => {
              const box = annotation.bbox
              if (!Array.isArray(box) || box.length !== 4) return null
              const [xMin, yMin, xMax, yMax] = box
              const isActive = annotation.id === activeId
              const color = confidenceColor(annotation.confidence ?? undefined)
              return (
                <div
                  key={annotation.id ?? `${xMin}-${yMin}`}
                  onClick={() => setActiveId(annotation.id ?? null)}
                  title={`${CATEGORY_LABEL[String(annotation.category)] ?? annotation.category} · ${STATUS_LABEL[annotation.status] ?? annotation.status}`}
                  style={{
                    position: 'absolute',
                    left: xMin * renderSize.scaleX,
                    top: yMin * renderSize.scaleY,
                    width: Math.max((xMax - xMin) * renderSize.scaleX, 2),
                    height: Math.max((yMax - yMin) * renderSize.scaleY, 2),
                    border: `2px solid ${color}`,
                    background: isActive ? `${color}33` : 'transparent',
                    boxShadow: isActive ? `0 0 0 2px ${color}55` : 'none',
                    cursor: 'pointer',
                    boxSizing: 'border-box',
                  }}
                />
              )
            })
          : null}
      </div>

      {/* 补框 */}
      <Space>
        <Button size="small" onClick={() => setShowAddForm((value) => !value)}>
          {showAddForm ? '取消补框' : '补框'}
        </Button>
        <Text type="secondary">红=低置信优先审 · 黄=中 · 绿=高</Text>
      </Space>
      {showAddForm ? (
        <Form layout="inline" style={{ rowGap: 8 }}>
          <Form.Item label="类别" style={{ marginBottom: 8 }}>
            <Select<SymbolCategory>
              style={{ width: 140 }}
              value={newCategory}
              onChange={setNewCategory}
              options={CATEGORY_OPTIONS}
            />
          </Form.Item>
          {(['x_min', 'y_min', 'x_max', 'y_max'] as const).map((label, index) => (
            <Form.Item key={label} label={label} style={{ marginBottom: 8 }}>
              <InputNumber
                style={{ width: 80 }}
                value={newBbox[index]}
                onChange={(value) => {
                  const next: [number, number, number, number] = [...newBbox]
                  next[index] = typeof value === 'number' ? value : 0
                  setNewBbox(next)
                }}
              />
            </Form.Item>
          ))}
          <Form.Item style={{ marginBottom: 8 }}>
            <Button
              type="primary"
              loading={savingKey === 'addbox'}
              onClick={async () => {
                await submitAction('addbox', { category: newCategory, bbox: newBbox, source: 'human', confidence: 1 }, 'addbox')
                setShowAddForm(false)
                setNewBbox(DEFAULT_BBOX)
              }}
            >
              新增框
            </Button>
          </Form.Item>
        </Form>
      ) : null}

      {/* 候选列表（低置信优先）+ 逐项动作 */}
      <Spin spinning={loading}>
        {sortedAnnotations.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无符号候选" />
        ) : (
          <List
            size="small"
            dataSource={sortedAnnotations}
            renderItem={(annotation) => {
              const key = String(annotation.id)
              return (
                <List.Item
                  onClick={() => setActiveId(annotation.id ?? null)}
                  style={{
                    cursor: 'pointer',
                    background: annotation.id === activeId ? '#f5f5f5' : undefined,
                  }}
                >
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    <Space wrap>
                      <ConfidenceDot confidence={annotation.confidence} />
                      <Tag>{CATEGORY_LABEL[String(annotation.category)] ?? annotation.category}</Tag>
                      <Tag color={annotation.source === 'human' ? 'blue' : 'default'}>
                        {annotation.source}
                      </Tag>
                      <Tag color={annotation.status === 'confirmed' ? 'green' : annotation.status === 'rejected' ? 'red' : 'gold'}>
                        {STATUS_LABEL[annotation.status] ?? annotation.status}
                      </Tag>
                      {typeof annotation.confidence === 'number' ? (
                        <Text type="secondary">{Math.round(annotation.confidence * 100)}%</Text>
                      ) : null}
                    </Space>
                    <Space wrap>
                      <Button
                        size="small"
                        type="primary"
                        loading={savingKey === `confirm-${key}`}
                        onClick={() => submitAction('confirm', { id: annotation.id }, `confirm-${key}`)}
                      >
                        确认
                      </Button>
                      <Button
                        size="small"
                        danger
                        loading={savingKey === `reject-${key}`}
                        onClick={() => submitAction('reject', { id: annotation.id }, `reject-${key}`)}
                      >
                        否定
                      </Button>
                      <Select<SymbolCategory>
                        size="small"
                        style={{ width: 120 }}
                        placeholder="改类"
                        value={undefined}
                        options={CATEGORY_OPTIONS}
                        onChange={(value) =>
                          submitAction(
                            'reclass',
                            { id: annotation.id, category: value },
                            `reclass-${key}`,
                          )
                        }
                      />
                    </Space>
                  </Space>
                </List.Item>
              )
            }}
          />
        )}
      </Spin>
    </Space>
  )
}

// ── 组合：楼层归属 + 符号标注 两个 Tab ─────────────────────────

export default function DrawingAnnotationQueue({
  items,
  buildingUnits,
  storyOptionsByBuilding,
  onSave,
  projectId,
  symbolDrawings,
}: DrawingAnnotationQueueProps) {
  const routeParams = useParams()
  const resolvedProjectId = projectId ?? (routeParams.projectId as string | undefined)
  const [tab, setTab] = useState<'floor' | 'symbol'>('floor')

  const symbolOptions: SymbolDrawingOption[] = useMemo(
    () =>
      symbolDrawings ??
      items.map((item) => ({
        drawingId: item.drawingId,
        title: `${item.drawingNo} ${item.title}`,
        thumbnailUrl: item.thumbnailUrl,
      })),
    [symbolDrawings, items],
  )

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Segmented
        block
        value={tab}
        onChange={(value) => setTab(value as 'floor' | 'symbol')}
        options={[
          { label: '楼层归属', value: 'floor' },
          { label: '符号标注', value: 'symbol' },
        ]}
      />
      {tab === 'floor' ? (
        <FloorAssignmentQueue
          items={items}
          buildingUnits={buildingUnits}
          storyOptionsByBuilding={storyOptionsByBuilding}
          onSave={onSave}
        />
      ) : (
        <SymbolAnnotationBoard projectId={resolvedProjectId} drawings={symbolOptions} />
      )}
    </Space>
  )
}

export { SymbolAnnotationBoard }
