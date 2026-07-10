import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApartmentOutlined,
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  EyeOutlined,
  ReloadOutlined,
  ScissorOutlined,
  SwapOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Badge,
  Button,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  message,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { SemanticOperationType } from '@/services/projectModel'
import type {
  CandidateSource,
  ReviewActionType,
  ReviewTargetKind,
} from '@/services/modelReview'
import { confidenceColor, getReviewQueue, submitReviewAction } from '@/services/modelReview'
import type {
  SemanticOperationDraft,
  SemanticOperationOutcome,
  SemanticOperationPreview,
  SemanticReviewItemView,
} from './types'

const { Paragraph, Text } = Typography

interface SemanticReviewQueueProps {
  items: SemanticReviewItemView[]
  nodeNameById: Record<string, string>
  onPreviewOperation: (draft: SemanticOperationDraft) => Promise<SemanticOperationPreview>
  onSubmitOperation: (draft: SemanticOperationDraft) => Promise<SemanticOperationOutcome>
  onSelectNode?: (nodeId: string) => void
  onRefreshRequested?: () => Promise<void>
  /** C-15：传入后激活「拓扑/命名/规范符合性」模型审校队列（低置信/冲突优先）。 */
  projectId?: string
}

// ── C-15 模型审校队列（拓扑闭合 / 构件命名 / 规范符合性）────────────────

/** 审校队列项（对齐后端 model_review.build_review_queue 输出契约）。 */
interface ReviewQueueItem {
  id: string
  target_kind: ReviewTargetKind
  title: string
  detail?: string
  confidence?: number | null
  source?: CandidateSource | null
  conflict?: boolean
  category?: string | null
  suggested_category?: string | null
  discipline?: string | null
  mep_system?: string | null
  drawing_id?: string | null
  priority?: number
}

interface ReviewQueueSummary {
  total: number
  conflict_count: number
  low_confidence_count: number
  by_kind: Record<string, number>
}

const KIND_META: Record<ReviewTargetKind, { label: string; color: string }> = {
  topology: { label: '拓扑闭合', color: 'geekblue' },
  naming: { label: '构件命名', color: 'purple' },
  compliance: { label: '规范符合性', color: 'volcano' },
  element: { label: '构件', color: 'blue' },
  symbol: { label: '符号', color: 'cyan' },
}

const CATEGORY_OPTIONS = [
  'column', 'beam', 'slab', 'wall', 'door', 'window', 'pipe', 'equipment', 'axis',
].map((value) => ({ value, label: value }))

function ModelReviewQueue({
  projectId,
  onSelectNode,
}: {
  projectId: string
  onSelectNode?: (nodeId: string) => void
}) {
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [summary, setSummary] = useState<ReviewQueueSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [reclassItem, setReclassItem] = useState<ReviewQueueItem | null>(null)
  const [newCategory, setNewCategory] = useState<string | undefined>(undefined)
  const [submittingId, setSubmittingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await getReviewQueue(projectId)
      const data = (resp as { data?: { items?: ReviewQueueItem[]; summary?: ReviewQueueSummary } })?.data
      setItems(data?.items ?? [])
      setSummary(data?.summary ?? null)
    } catch (error) {
      message.error('加载审校队列失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  const submit = useCallback(
    async (
      item: ReviewQueueItem,
      actionType: ReviewActionType,
      extra?: { newCategory?: string },
    ) => {
      setSubmittingId(item.id)
      try {
        await submitReviewAction(projectId, {
          projectId,
          drawingId: item.drawing_id ?? undefined,
          targetKind: item.target_kind,
          targetId: item.id,
          actionType,
          oldCategory: item.category ?? undefined,
          newCategory: extra?.newCategory,
          mepSystem: item.mep_system ?? undefined,
          discipline: item.discipline ?? undefined,
          source: item.source ?? undefined,
          confidence: item.confidence ?? undefined,
        })
        message.success('已记录审校动作')
        setItems((current) =>
          current.filter((it) => !(it.id === item.id && it.target_kind === item.target_kind)),
        )
      } catch (error) {
        message.error('提交审校动作失败')
      } finally {
        setSubmittingId(null)
      }
    },
    [projectId],
  )

  const headerExtra = (
    <Space size={6}>
      {summary && summary.conflict_count > 0 ? (
        <Tag color="red" icon={<ThunderboltOutlined />}>冲突 {summary.conflict_count}</Tag>
      ) : null}
      {summary && summary.low_confidence_count > 0 ? (
        <Tag color="orange">低置信 {summary.low_confidence_count}</Tag>
      ) : null}
      <Tooltip title="刷新队列">
        <Button
          size="small"
          type="text"
          icon={<ReloadOutlined />}
          aria-label="刷新审校队列"
          loading={loading}
          onClick={() => void load()}
        />
      </Tooltip>
    </Space>
  )

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text strong>成果审校（拓扑 / 命名 / 规范）</Text>
        {headerExtra}
      </Space>

      {loading && items.length === 0 ? (
        <Spin size="small" />
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待审成果" />
      ) : (
        <List
          size="small"
          dataSource={items}
          rowKey={(item) => `${item.target_kind}:${item.id}`}
          renderItem={(item) => {
            const busy = submittingId === item.id
            const kindMeta = KIND_META[item.target_kind]
            return (
              <List.Item
                style={item.conflict ? { borderLeft: '3px solid #ff4d4f', paddingLeft: 8 } : undefined}
              >
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Space direction="vertical" size={2}>
                      <Space wrap size={4}>
                        {item.conflict ? <Badge status="error" /> : null}
                        <Text strong>{item.title}</Text>
                        <Tag color={kindMeta?.color}>{kindMeta?.label ?? item.target_kind}</Tag>
                        {item.confidence != null ? (
                          <Tag color={confidenceColor(item.confidence)}>
                            {Math.round(item.confidence * 100)}%
                          </Tag>
                        ) : null}
                        {item.conflict ? <Tag color="red">规则-模型冲突/未闭合</Tag> : null}
                        {item.source ? <Tag>{item.source}</Tag> : null}
                        {item.discipline ? <Tag color="default">{item.discipline}</Tag> : null}
                      </Space>
                      {item.detail ? <Text type="secondary">{item.detail}</Text> : null}
                    </Space>
                    <Space size={2}>
                      <Tooltip title="确认">
                        <Button
                          type="text"
                          size="small"
                          icon={<CheckOutlined />}
                          aria-label={`确认 ${item.title}`}
                          loading={busy}
                          onClick={() => {
                            if (item.drawing_id) onSelectNode?.(item.drawing_id)
                            void submit(item, 'confirm')
                          }}
                        />
                      </Tooltip>
                      <Tooltip title="改类">
                        <Button
                          type="text"
                          size="small"
                          icon={<SwapOutlined />}
                          aria-label={`改类 ${item.title}`}
                          disabled={busy}
                          onClick={() => {
                            setReclassItem(item)
                            setNewCategory(item.suggested_category ?? undefined)
                          }}
                        />
                      </Tooltip>
                      <Tooltip title="否定">
                        <Button
                          type="text"
                          size="small"
                          icon={<CloseOutlined />}
                          aria-label={`否定 ${item.title}`}
                          loading={busy}
                          onClick={() => void submit(item, 'reject')}
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
        open={Boolean(reclassItem)}
        title={reclassItem ? `改类：${reclassItem.title}` : undefined}
        okText="提交改类"
        okButtonProps={{ disabled: !newCategory }}
        onCancel={() => {
          setReclassItem(null)
          setNewCategory(undefined)
        }}
        onOk={async () => {
          if (!reclassItem || !newCategory) return
          await submit(reclassItem, 'reclass', { newCategory })
          setReclassItem(null)
          setNewCategory(undefined)
        }}
        destroyOnClose
      >
        {reclassItem ? (
          <Form layout="vertical">
            <Form.Item label="当前类别" style={{ marginBottom: 12 }}>
              <Text>{reclassItem.category ?? '未标注'}</Text>
            </Form.Item>
            <Form.Item label="改后类别" style={{ marginBottom: 0 }}>
              <Select
                aria-label="改后类别"
                options={CATEGORY_OPTIONS}
                value={newCategory}
                onChange={(value) => setNewCategory(value)}
                placeholder="请选择新的构件类别"
                showSearch
              />
            </Form.Item>
          </Form>
        ) : null}
      </Modal>
    </Space>
  )
}

interface DialogState {
  item: SemanticReviewItemView
  operation: SemanticOperationType
}

const TYPE_LABEL = {
  building_unit: '单体',
  sub_zone: '分区',
  functional_space: '功能空间',
  construction_zone: '施工分区',
} as const

const STATUS_META = {
  candidate: { label: '候选', color: 'gold' },
  confirmed: { label: '已确认', color: 'green' },
  rejected: { label: '已拒绝', color: 'default' },
  merged: { label: '已合并', color: 'blue' },
} as const

const SUBMIT_LABEL: Record<SemanticOperationType, string> = {
  confirm: '提交确认',
  reject: '提交拒绝',
  rename: '提交重命名',
  merge: '提交合并',
  split: '提交拆分',
  reparent: '提交父级调整',
}

const DIALOG_TITLE: Record<SemanticOperationType, string> = {
  confirm: '确认语义节点',
  reject: '拒绝语义节点',
  rename: '重命名语义节点',
  merge: '合并语义节点',
  split: '拆分语义节点',
  reparent: '调整父级',
}

function buildDraft(
  dialogState: DialogState | null,
  targetNodeId: string | undefined,
  newName: string,
  splitNamesText: string,
): SemanticOperationDraft | null {
  if (!dialogState) return null
  const base = {
    operation: dialogState.operation,
    nodeId: dialogState.item.nodeId,
    version: dialogState.item.version,
  } satisfies SemanticOperationDraft

  switch (dialogState.operation) {
    case 'confirm':
    case 'reject':
      return base
    case 'rename':
      return newName.trim() ? { ...base, newName: newName.trim() } : null
    case 'merge':
    case 'reparent':
      return targetNodeId ? { ...base, targetNodeId } : null
    case 'split': {
      const splitNames = splitNamesText
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean)
      return splitNames.length >= 2 ? { ...base, splitNames } : null
    }
    default:
      return null
  }
}

export default function SemanticReviewQueue({
  items,
  nodeNameById,
  onPreviewOperation,
  onSubmitOperation,
  onSelectNode,
  onRefreshRequested,
  projectId,
}: SemanticReviewQueueProps) {
  const [evidenceItem, setEvidenceItem] = useState<SemanticReviewItemView | null>(null)
  const [dialogState, setDialogState] = useState<DialogState | null>(null)
  const [targetNodeId, setTargetNodeId] = useState<string | undefined>(undefined)
  const [newName, setNewName] = useState('')
  const [splitNamesText, setSplitNamesText] = useState('')
  const [preview, setPreview] = useState<SemanticOperationPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [staleVersions, setStaleVersions] = useState<Record<string, number>>({})

  const targetOptions = useMemo(() => {
    if (!dialogState) return []
    const nodeIds = dialogState.operation === 'merge'
      ? dialogState.item.mergeTargets
      : dialogState.operation === 'reparent'
        ? dialogState.item.reparentTargets
        : []
    return nodeIds.map((nodeId) => ({
      value: nodeId,
      label: nodeNameById[nodeId] ?? nodeId,
    }))
  }, [dialogState, nodeNameById])

  const currentDraft = useMemo(
    () => buildDraft(dialogState, targetNodeId, newName, splitNamesText),
    [dialogState, targetNodeId, newName, splitNamesText],
  )

  useEffect(() => {
    if (!dialogState) return
    setPreview(null)
    if (!currentDraft) return
    let alive = true
    setPreviewLoading(true)
    onPreviewOperation(currentDraft)
      .then((result) => {
        if (alive) setPreview(result)
      })
      .finally(() => {
        if (alive) setPreviewLoading(false)
      })
    return () => {
      alive = false
    }
  }, [currentDraft, dialogState, onPreviewOperation])

  const reviewQueue = projectId ? (
    <ModelReviewQueue projectId={projectId} onSelectNode={onSelectNode} />
  ) : null

  if (items.length === 0) {
    return (
      <>
        {reviewQueue}
        {projectId ? <Divider style={{ margin: '12px 0' }} /> : null}
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待审语义候选" />
      </>
    )
  }

  return (
    <>
      {reviewQueue}
      {projectId ? <Divider style={{ margin: '12px 0' }} /> : null}
      <List
        itemLayout="vertical"
        dataSource={items}
        renderItem={(item) => (
          <List.Item key={item.nodeId}>
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space wrap>
                    <Text strong>{item.title}</Text>
                    <Tag>{TYPE_LABEL[item.nodeType]}</Tag>
                    <Tag color={STATUS_META[item.status].color}>{STATUS_META[item.status].label}</Tag>
                    {item.confidence > 0 ? (
                      <Tag color="gold">{Math.round(item.confidence * 100)}%</Tag>
                    ) : null}
                  </Space>
                  {item.currentParentName ? (
                    <Text type="secondary">当前父级 {item.currentParentName}</Text>
                  ) : null}
                </Space>
                <Space size={4}>
                  <Tooltip title="查看证据">
                    <Button
                      type="text"
                      icon={<EyeOutlined />}
                      aria-label={`查看证据 ${item.title}`}
                      onClick={() => {
                        onSelectNode?.(item.nodeId)
                        setEvidenceItem(item)
                      }}
                    />
                  </Tooltip>
                  <Tooltip title="确认">
                    <Button
                      type="text"
                      icon={<CheckOutlined />}
                      aria-label={`确认${item.title}`}
                      onClick={() => {
                        onSelectNode?.(item.nodeId)
                        setDialogState({ item, operation: 'confirm' })
                        setTargetNodeId(undefined)
                        setNewName(item.canonicalName)
                        setSplitNamesText('')
                      }}
                    />
                  </Tooltip>
                  <Tooltip title="调整父级">
                    <Button
                      type="text"
                      icon={<ApartmentOutlined />}
                      disabled={item.reparentTargets.length === 0}
                      aria-label={`调整父级 ${item.title}`}
                      onClick={() => {
                        onSelectNode?.(item.nodeId)
                        setDialogState({ item, operation: 'reparent' })
                        setTargetNodeId(undefined)
                        setNewName(item.canonicalName)
                        setSplitNamesText('')
                      }}
                    />
                  </Tooltip>
                  <Tooltip title="重命名">
                    <Button
                      type="text"
                      icon={<EditOutlined />}
                      aria-label={`重命名 ${item.title}`}
                      onClick={() => {
                        onSelectNode?.(item.nodeId)
                        setDialogState({ item, operation: 'rename' })
                        setTargetNodeId(undefined)
                        setNewName(item.canonicalName)
                        setSplitNamesText('')
                      }}
                    />
                  </Tooltip>
                  <Tooltip title="合并">
                    <Button
                      type="text"
                      icon={<SwapOutlined />}
                      disabled={item.mergeTargets.length === 0}
                      aria-label={`合并 ${item.title}`}
                      onClick={() => {
                        onSelectNode?.(item.nodeId)
                        setDialogState({ item, operation: 'merge' })
                        setTargetNodeId(undefined)
                        setNewName(item.canonicalName)
                        setSplitNamesText('')
                      }}
                    />
                  </Tooltip>
                  <Tooltip title="拆分">
                    <Button
                      type="text"
                      icon={<ScissorOutlined />}
                      aria-label={`拆分 ${item.title}`}
                      onClick={() => {
                        onSelectNode?.(item.nodeId)
                        setDialogState({ item, operation: 'split' })
                        setTargetNodeId(undefined)
                        setNewName(item.canonicalName)
                        setSplitNamesText(`${item.canonicalName} A\n${item.canonicalName} B`)
                      }}
                    />
                  </Tooltip>
                  <Tooltip title="拒绝">
                    <Button
                      type="text"
                      icon={<CloseOutlined />}
                      aria-label={`拒绝 ${item.title}`}
                      onClick={() => {
                        onSelectNode?.(item.nodeId)
                        setDialogState({ item, operation: 'reject' })
                        setTargetNodeId(undefined)
                        setNewName(item.canonicalName)
                        setSplitNamesText('')
                      }}
                    />
                  </Tooltip>
                </Space>
              </Space>

              {staleVersions[item.nodeId] ? (
                <Alert
                  type="warning"
                  showIcon
                  message="语义树版本已更新"
                  description="当前候选基于旧版本，刷新后再重试。"
                  action={(
                    <Button
                      size="small"
                      onClick={async () => {
                        await onRefreshRequested?.()
                        setStaleVersions((current) => {
                          const next = { ...current }
                          delete next[item.nodeId]
                          return next
                        })
                      }}
                    >
                      刷新到 v{staleVersions[item.nodeId]}
                    </Button>
                  )}
                />
              ) : null}
            </Space>
          </List.Item>
        )}
      />

      <Drawer
        open={Boolean(evidenceItem)}
        width={380}
        title={evidenceItem ? `${evidenceItem.title} 证据` : undefined}
        onClose={() => setEvidenceItem(null)}
        footer={(
          <Button onClick={() => setEvidenceItem(null)}>
            关闭证据
          </Button>
        )}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {(evidenceItem?.evidence ?? []).map((evidence) => (
            <Alert
              key={evidence.id}
              type="info"
              showIcon
              message={(
                <Space wrap>
                  <Text strong>{evidence.label}</Text>
                  {typeof evidence.score === 'number' ? (
                    <Tag color="gold">{Math.round(evidence.score * 100)}%</Tag>
                  ) : null}
                </Space>
              )}
              description={(
                <Space direction="vertical" size={4}>
                  <Text>{evidence.detail}</Text>
                  {evidence.sourceDrawingId ? (
                    <Text type="secondary">来源图纸 {evidence.sourceDrawingId}</Text>
                  ) : null}
                </Space>
              )}
            />
          ))}
          {(evidenceItem?.evidence.length ?? 0) === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无候选证据" />
          ) : null}
        </Space>
      </Drawer>

      <Modal
        open={Boolean(dialogState)}
        title={dialogState ? DIALOG_TITLE[dialogState.operation] : undefined}
        onCancel={() => {
          setDialogState(null)
          setPreview(null)
        }}
        destroyOnClose
        footer={[
          <Button
            key="cancel"
            onClick={() => {
              setDialogState(null)
              setPreview(null)
            }}
          >
            取消
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={submitting}
            disabled={!currentDraft}
            onClick={async () => {
              if (!currentDraft || !dialogState) return
              setSubmitting(true)
              try {
                const result = await onSubmitOperation(currentDraft)
                if (result.ok) {
                  setDialogState(null)
                  setPreview(null)
                  return
                }
                if (result.staleVersion) {
                  setStaleVersions((current) => ({
                    ...current,
                    [dialogState.item.nodeId]: result.staleVersion!,
                  }))
                  setDialogState(null)
                  setPreview(null)
                }
              } finally {
                setSubmitting(false)
              }
            }}
          >
            {dialogState ? SUBMIT_LABEL[dialogState.operation] : '提交'}
          </Button>,
        ]}
      >
        {dialogState ? (
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <Text strong>{dialogState.item.title}</Text>
            {dialogState.operation === 'rename' ? (
              <Form layout="vertical">
                <Form.Item label="新的名称" style={{ marginBottom: 0 }}>
                  <Input
                    aria-label="新的名称"
                    value={newName}
                    onChange={(event) => setNewName(event.target.value)}
                  />
                </Form.Item>
              </Form>
            ) : null}

            {(dialogState.operation === 'merge' || dialogState.operation === 'reparent') ? (
              <Form layout="vertical">
                <Form.Item
                  label={dialogState.operation === 'merge' ? '合并目标' : '新的父级'}
                  style={{ marginBottom: 0 }}
                >
                  <Select
                    aria-label={dialogState.operation === 'merge' ? '合并目标' : '新的父级'}
                    options={targetOptions}
                    value={targetNodeId}
                    onChange={(value) => setTargetNodeId(value)}
                    placeholder="请选择目标节点"
                  />
                </Form.Item>
              </Form>
            ) : null}

            {dialogState.operation === 'split' ? (
              <Form layout="vertical">
                <Form.Item label="拆分名称（每行一个）" style={{ marginBottom: 0 }}>
                  <Input.TextArea
                    aria-label="拆分名称"
                    autoSize={{ minRows: 3, maxRows: 6 }}
                    value={splitNamesText}
                    onChange={(event) => setSplitNamesText(event.target.value)}
                  />
                </Form.Item>
              </Form>
            ) : null}

            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Text strong>影响范围</Text>
              {previewLoading ? (
                <Spin size="small" />
              ) : preview ? (
                <Alert
                  type="info"
                  showIcon
                  message={preview.summary}
                  description={(
                    <Space direction="vertical" size={4}>
                      {preview.affected_scope.map((scope) => (
                        <Text key={scope}>{scope}</Text>
                      ))}
                      {preview.fallback_reason ? (
                        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                          {preview.fallback_reason}
                        </Paragraph>
                      ) : null}
                    </Space>
                  )}
                />
              ) : (
                <Text type="secondary">填写完整后显示重建影响范围。</Text>
              )}
            </Space>
          </Space>
        ) : null}
      </Modal>
    </>
  )
}
