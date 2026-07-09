import { useEffect, useMemo, useState } from 'react'
import {
  ApartmentOutlined,
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  EyeOutlined,
  ScissorOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  List,
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

  if (items.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待审语义候选" />
  }

  return (
    <>
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
