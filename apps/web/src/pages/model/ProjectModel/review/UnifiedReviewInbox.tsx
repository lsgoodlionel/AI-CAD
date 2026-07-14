/**
 * D-14 审校收件箱：合并原 DrawingAnnotationQueue「符号标注」与
 * SemanticReviewQueue「成果审校（拓扑/命名/规范）」两个队列为一个列表。
 *
 * - 数据源不变、端点不变：symbol 走 listSymbolAnnotations/saveSymbolAnnotation
 *   （model_symbol_annotations 表），semantic 走 getReviewQueue/submitReviewAction
 *   （model_review_actions 埋点）。C-17 返工看板依赖这两条埋点，字段/调用方式原样保留。
 * - 排序：见 review/reviewInbox.ts sortInboxItems（冲突优先→低置信优先）。
 * - 键盘快捷键：↑/↓ 或 j/k 移动焦点，c 确认，x 否定，r 改类 —— 单条操作 1 击键。
 * - 图纸选择 + 候选框叠加预览 + 补框表单见 SymbolOverlayPicker.tsx（拆出以保持本文件聚焦
 *   「统一列表 + 动作分派 + 快捷键」）。
 *
 * 「楼层归属」（表单式录入，非候选确认/否定）与「语义树候选」（merge/split/rename +
 * 影响预览，动作语义与本收件箱不同）保留为独立组件，不纳入本次合并（见 FloorAssignmentQueue.tsx /
 * SemanticCandidateQueue.tsx 及 docs 说明）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Badge, Button, Empty, Form, List, Modal, Select, Space, Spin, Tag, Tooltip, Typography, message } from 'antd'
import { CheckOutlined, CloseOutlined, ReloadOutlined, SwapOutlined, ThunderboltOutlined } from '@ant-design/icons'
import type { ReviewActionType, SymbolAnnotation, SymbolCategory } from '@/services/modelReview'
import { getReviewQueue, listSymbolAnnotations, saveSymbolAnnotation, submitReviewAction } from '@/services/modelReview'
import {
  CATEGORY_LABEL, CATEGORY_OPTIONS, TARGET_KIND_LABEL,
  confidenceColor, fromSemanticQueueRow, fromSymbolAnnotation, sortInboxItems,
} from './reviewInbox'
import type { ReviewInboxItem, SemanticQueueRow, SemanticQueueSummary, SymbolDrawingOption } from './reviewInbox'
import SymbolOverlayPicker from './SymbolOverlayPicker'

const { Text } = Typography

interface UnifiedReviewInboxProps {
  projectId: string
  /** 可做符号标注的图纸（缺省来自「待人工识别」队列的图纸缩略图，与原行为一致）。 */
  symbolDrawings: SymbolDrawingOption[]
  /** semantic 候选确认时回传所属图纸，供上层在语义树/场景中定位（对齐原 ModelReviewQueue 行为）。 */
  onSelectNode?: (drawingId: string) => void
}

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  const tag = el?.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || Boolean(el?.isContentEditable)
}

export default function UnifiedReviewInbox({
  projectId,
  symbolDrawings,
  onSelectNode,
}: UnifiedReviewInboxProps) {
  const [semanticItems, setSemanticItems] = useState<ReviewInboxItem[]>([])
  const [semanticSummary, setSemanticSummary] = useState<SemanticQueueSummary | null>(null)
  const [symbolItems, setSymbolItems] = useState<ReviewInboxItem[]>([])
  const [selectedDrawingId, setSelectedDrawingId] = useState<string | undefined>(symbolDrawings[0]?.drawingId)
  const [loadingSemantic, setLoadingSemantic] = useState(false)
  const [loadingSymbol, setLoadingSymbol] = useState(false)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [reclassTarget, setReclassTarget] = useState<ReviewInboxItem | null>(null)
  const [reclassValue, setReclassValue] = useState<SymbolCategory | undefined>(undefined)

  const loadSemantic = useCallback(async () => {
    setLoadingSemantic(true)
    try {
      const resp = await getReviewQueue(projectId)
      const data = (resp as { data?: { items?: SemanticQueueRow[]; summary?: SemanticQueueSummary } })?.data
      setSemanticItems((data?.items ?? []).map(fromSemanticQueueRow))
      setSemanticSummary(data?.summary ?? null)
    } catch {
      message.error('加载审校队列失败')
    } finally {
      setLoadingSemantic(false)
    }
  }, [projectId])

  const loadSymbol = useCallback(async () => {
    if (!selectedDrawingId) {
      setSymbolItems([])
      return
    }
    setLoadingSymbol(true)
    try {
      const resp = await listSymbolAnnotations(projectId, selectedDrawingId)
      const list = Array.isArray(resp?.data) ? resp.data : []
      setSymbolItems(list.map((a: SymbolAnnotation) => fromSymbolAnnotation(selectedDrawingId, a)))
    } catch {
      message.error('加载符号标注失败')
      setSymbolItems([])
    } finally {
      setLoadingSymbol(false)
    }
  }, [projectId, selectedDrawingId])

  useEffect(() => { void loadSemantic() }, [loadSemantic])
  useEffect(() => { void loadSymbol() }, [loadSymbol])

  const items = useMemo(() => sortInboxItems([...semanticItems, ...symbolItems]), [semanticItems, symbolItems])

  useEffect(() => {
    if (items.length === 0) { setActiveKey(null); return }
    if (!items.some((i) => i.key === activeKey)) setActiveKey(items[0].key)
  }, [items, activeKey])

  const submit = useCallback(async (
    item: ReviewInboxItem,
    actionType: ReviewActionType,
    extra?: { newCategory?: SymbolCategory },
  ) => {
    setBusyKey(item.key)
    try {
      if (item.kind === 'semantic') {
        const row = item.raw as SemanticQueueRow
        await submitReviewAction(projectId, {
          projectId,
          drawingId: row.drawing_id ?? undefined,
          targetKind: row.target_kind,
          targetId: row.id,
          actionType,
          oldCategory: row.category ?? undefined,
          newCategory: extra?.newCategory,
          mepSystem: row.mep_system ?? undefined,
          discipline: row.discipline ?? undefined,
          source: row.source ?? undefined,
          confidence: row.confidence ?? undefined,
        })
        if (actionType === 'confirm' && row.drawing_id) onSelectNode?.(row.drawing_id)
        message.success('已记录审校动作')
        await loadSemantic()
      } else {
        const annotation = item.raw as SymbolAnnotation
        if (!item.drawingId) throw new Error('missing drawing id')
        await saveSymbolAnnotation(projectId, item.drawingId, {
          id: annotation.id,
          category: extra?.newCategory ?? annotation.category,
          actionType,
        })
        message.success('已保存并记录人审')
        await loadSymbol()
      }
    } catch {
      message.error('提交审校动作失败')
    } finally {
      setBusyKey((current) => (current === item.key ? null : current))
    }
  }, [projectId, onSelectNode, loadSemantic, loadSymbol])

  const submitAddBox = useCallback(async (category: SymbolCategory, bbox: [number, number, number, number]) => {
    if (!selectedDrawingId) { message.warning('请先选择图纸'); return }
    setBusyKey('addbox')
    try {
      await saveSymbolAnnotation(projectId, selectedDrawingId, {
        category, bbox, source: 'human', confidence: 1, actionType: 'addbox',
      })
      message.success('已新增候选框')
      await loadSymbol()
    } catch {
      message.error('新增失败')
    } finally {
      setBusyKey(null)
    }
  }, [projectId, selectedDrawingId, loadSymbol])

  // 键盘快捷键流水作业：↑/↓ j/k 移动焦点，c 确认，x 否定，r 改类（单条操作 1 击键）
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (isTypingTarget(event.target) || reclassTarget || items.length === 0) return
      const idx = activeKey ? items.findIndex((i) => i.key === activeKey) : -1
      const current = idx >= 0 ? items[idx] : items[0]
      if (event.key === 'ArrowDown' || event.key === 'j') {
        event.preventDefault()
        setActiveKey(items[Math.min(idx + 1, items.length - 1)]?.key ?? items[0].key)
      } else if (event.key === 'ArrowUp' || event.key === 'k') {
        event.preventDefault()
        setActiveKey(items[Math.max(idx - 1, 0)]?.key ?? items[0].key)
      } else if (event.key === 'c') {
        void submit(current, 'confirm')
      } else if (event.key === 'x') {
        void submit(current, 'reject')
      } else if (event.key === 'r') {
        setReclassTarget(current)
        setReclassValue((current.suggestedCategory ?? current.category) as SymbolCategory | undefined)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [items, activeKey, reclassTarget, submit])

  const conflictCount = (semanticSummary?.conflict_count ?? 0)
  const lowConfidenceCount = (semanticSummary?.low_confidence_count ?? 0)
    + symbolItems.filter((i) => (i.confidence ?? 1) < 0.5).length

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Space size={6} wrap>
          {conflictCount > 0 ? <Tag color="red" icon={<ThunderboltOutlined />}>冲突 {conflictCount}</Tag> : null}
          {lowConfidenceCount > 0 ? <Tag color="orange">低置信 {lowConfidenceCount}</Tag> : null}
          <Text type="secondary" style={{ fontSize: 12 }}>快捷键：j/k 移动 · c 确认 · x 否定 · r 改类</Text>
        </Space>
        <Tooltip title="刷新收件箱">
          <Button
            size="small" type="text" icon={<ReloadOutlined />} aria-label="刷新审校收件箱"
            loading={loadingSemantic || loadingSymbol}
            onClick={() => { void loadSemantic(); void loadSymbol() }}
          />
        </Tooltip>
      </Space>

      <SymbolOverlayPicker
        drawings={symbolDrawings}
        selectedDrawingId={selectedDrawingId}
        onSelectDrawing={setSelectedDrawingId}
        symbolItems={symbolItems}
        activeKey={activeKey}
        onActivateItem={setActiveKey}
        onAddBox={submitAddBox}
        addBoxBusy={busyKey === 'addbox'}
      />

      {(loadingSemantic || loadingSymbol) && items.length === 0 ? (
        <Spin size="small" />
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待审候选" />
      ) : (
        <List
          size="small"
          dataSource={items}
          rowKey={(item) => item.key}
          renderItem={(item) => {
            const busy = busyKey === item.key
            const kindMeta = item.kind === 'semantic' && item.targetKind
              ? TARGET_KIND_LABEL[item.targetKind]
              : { label: `符号·${CATEGORY_LABEL[item.category ?? ''] ?? item.category ?? ''}`, color: 'cyan' }
            return (
              <List.Item
                onClick={() => setActiveKey(item.key)}
                style={{
                  cursor: 'pointer',
                  background: item.key === activeKey ? '#f5f5f5' : undefined,
                  borderLeft: item.conflict ? '3px solid #ff4d4f' : undefined,
                  paddingLeft: item.conflict ? 8 : undefined,
                }}
              >
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                    <Space direction="vertical" size={2}>
                      <Space wrap size={4}>
                        {item.conflict ? <Badge status="error" /> : null}
                        <Text strong>{item.title}</Text>
                        <Tag color={kindMeta.color}>{kindMeta.label}</Tag>
                        {item.confidence != null ? (
                          <Tag color={confidenceColor(item.confidence)}>{Math.round(item.confidence * 100)}%</Tag>
                        ) : null}
                        {item.conflict ? <Tag color="red">规则-模型冲突/未闭合</Tag> : null}
                        {item.source ? <Tag>{item.source}</Tag> : null}
                        {item.discipline ? <Tag color="default">{item.discipline}</Tag> : null}
                      </Space>
                      {item.detail ? <Text type="secondary">{item.detail}</Text> : null}
                    </Space>
                    <Space size={2}>
                      <Tooltip title="确认 (c)">
                        <Button
                          type="text" size="small" icon={<CheckOutlined />} aria-label={`确认 ${item.title}`}
                          loading={busy} onClick={() => void submit(item, 'confirm')}
                        />
                      </Tooltip>
                      <Tooltip title="改类 (r)">
                        <Button
                          type="text" size="small" icon={<SwapOutlined />} aria-label={`改类 ${item.title}`}
                          disabled={busy}
                          onClick={() => {
                            setReclassTarget(item)
                            setReclassValue((item.suggestedCategory ?? item.category) as SymbolCategory | undefined)
                          }}
                        />
                      </Tooltip>
                      <Tooltip title="否定 (x)">
                        <Button
                          type="text" size="small" danger icon={<CloseOutlined />} aria-label={`否定 ${item.title}`}
                          loading={busy} onClick={() => void submit(item, 'reject')}
                        />
                      </Tooltip>
                    </Space>
                  </Space>
                </Space>
              </List.Item>
            )
          }}
        />
      )}

      <Modal
        open={Boolean(reclassTarget)}
        title={reclassTarget ? `改类：${reclassTarget.title}` : undefined}
        okText="提交改类"
        okButtonProps={{ disabled: !reclassValue }}
        onCancel={() => { setReclassTarget(null); setReclassValue(undefined) }}
        onOk={async () => {
          if (!reclassTarget || !reclassValue) return
          await submit(reclassTarget, 'reclass', { newCategory: reclassValue })
          setReclassTarget(null)
          setReclassValue(undefined)
        }}
        destroyOnClose
      >
        {reclassTarget ? (
          <Form layout="vertical">
            <Form.Item label="当前类别" style={{ marginBottom: 12 }}>
              <Text>{CATEGORY_LABEL[reclassTarget.category ?? ''] ?? reclassTarget.category ?? '未标注'}</Text>
            </Form.Item>
            <Form.Item label="改后类别" style={{ marginBottom: 0 }}>
              <Select
                aria-label="改后类别"
                options={CATEGORY_OPTIONS}
                value={reclassValue}
                onChange={setReclassValue}
                placeholder="请选择新的构件类别"
                showSearch
              />
            </Form.Item>
          </Form>
        ) : null}
      </Modal>

      {loadingSemantic && semanticItems.length > 0 ? (
        <Alert type="info" showIcon message="正在刷新成果审校队列…" />
      ) : null}
    </Space>
  )
}
