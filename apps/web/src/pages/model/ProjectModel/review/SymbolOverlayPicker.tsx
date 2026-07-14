/**
 * 审校收件箱内嵌的图纸选择 + 符号候选框叠加预览 + 补框表单。
 * 从 UnifiedReviewInbox.tsx 拆出，保持该文件聚焦「统一列表 + 动作分派 + 快捷键」。
 */
import { useCallback, useRef, useState } from 'react'
import { Button, Divider, Form, InputNumber, Select, Space } from 'antd'
import type { SymbolAnnotation, SymbolCategory } from '@/services/modelReview'
import { CATEGORY_OPTIONS, confidenceColor } from './reviewInbox'
import type { ReviewInboxItem, SymbolDrawingOption } from './reviewInbox'

const DEFAULT_BBOX: [number, number, number, number] = [0, 0, 100, 100]

interface SymbolOverlayPickerProps {
  drawings: SymbolDrawingOption[]
  selectedDrawingId?: string
  onSelectDrawing: (drawingId: string) => void
  symbolItems: ReviewInboxItem[]
  activeKey: string | null
  onActivateItem: (key: string) => void
  onAddBox: (category: SymbolCategory, bbox: [number, number, number, number]) => Promise<void>
  addBoxBusy: boolean
}

export default function SymbolOverlayPicker({
  drawings,
  selectedDrawingId,
  onSelectDrawing,
  symbolItems,
  activeKey,
  onActivateItem,
  onAddBox,
  addBoxBusy,
}: SymbolOverlayPickerProps) {
  const [showAddForm, setShowAddForm] = useState(false)
  const [newCategory, setNewCategory] = useState<SymbolCategory>('column')
  const [newBbox, setNewBbox] = useState<[number, number, number, number]>(DEFAULT_BBOX)
  const [renderSize, setRenderSize] = useState({ scaleX: 1, scaleY: 1 })
  const imgRef = useRef<HTMLImageElement | null>(null)

  const selectedDrawing = drawings.find((d) => d.drawingId === selectedDrawingId)

  const measureImage = useCallback(() => {
    const el = imgRef.current
    if (!el || !el.naturalWidth || !el.naturalHeight) return
    setRenderSize({ scaleX: el.clientWidth / el.naturalWidth, scaleY: el.clientHeight / el.naturalHeight })
  }, [])

  if (drawings.length === 0) return null

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Select
        style={{ width: '100%' }}
        size="small"
        value={selectedDrawingId}
        placeholder="选择图纸查看符号候选框"
        onChange={(value) => { onSelectDrawing(value); setShowAddForm(false) }}
        options={drawings.map((d) => ({ value: d.drawingId, label: d.title }))}
      />
      {selectedDrawing?.thumbnailUrl ? (
        <div style={{ position: 'relative', width: '100%', border: '1px solid #f0f0f0', borderRadius: 6, overflow: 'hidden' }}>
          <img
            ref={imgRef}
            src={selectedDrawing.thumbnailUrl}
            alt={selectedDrawing.title}
            style={{ display: 'block', width: '100%', height: 'auto' }}
            onLoad={measureImage}
          />
          {symbolItems.map((item) => {
            const box = (item.raw as SymbolAnnotation).bbox
            if (!Array.isArray(box) || box.length !== 4) return null
            const [xMin, yMin, xMax, yMax] = box
            const color = confidenceColor(item.confidence)
            return (
              <div
                key={item.key}
                onClick={() => onActivateItem(item.key)}
                title={item.title}
                style={{
                  position: 'absolute',
                  left: xMin * renderSize.scaleX, top: yMin * renderSize.scaleY,
                  width: Math.max((xMax - xMin) * renderSize.scaleX, 2),
                  height: Math.max((yMax - yMin) * renderSize.scaleY, 2),
                  border: `2px solid ${color}`,
                  background: item.key === activeKey ? `${color}33` : 'transparent',
                  cursor: 'pointer', boxSizing: 'border-box',
                }}
              />
            )
          })}
        </div>
      ) : null}
      <Button size="small" onClick={() => setShowAddForm((v) => !v)}>
        {showAddForm ? '取消补框' : '补框'}
      </Button>
      {showAddForm ? (
        <Form layout="inline" style={{ rowGap: 8 }}>
          <Form.Item label="类别" style={{ marginBottom: 8 }}>
            <Select<SymbolCategory> style={{ width: 140 }} value={newCategory} onChange={setNewCategory} options={CATEGORY_OPTIONS} />
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
              loading={addBoxBusy}
              onClick={async () => {
                await onAddBox(newCategory, newBbox)
                setShowAddForm(false)
                setNewBbox(DEFAULT_BBOX)
              }}
            >
              新增框
            </Button>
          </Form.Item>
        </Form>
      ) : null}
      <Divider style={{ margin: '4px 0' }} />
    </Space>
  )
}
