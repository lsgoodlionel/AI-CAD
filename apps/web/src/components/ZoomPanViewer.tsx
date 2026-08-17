/**
 * 图纸缩放/平移查看器 —— 施工图信息密度高,不放大看不清标注与构件。
 *
 * 交互:滚轮以**指针为锚点**缩放(不是缩放到中心,否则目标会跑出视野)、
 * 按住拖拽平移、双击复位、工具栏放大/缩小/复位/适应宽度。
 * children 以 transform 承载,可叠加绝对定位的标记层(回投核对复用)。
 *
 * `panDisabled`:标注(框选/画线)时必须关掉平移,否则一拖就变成拖动图纸,
 * 根本框不出来。缩放与复位仍可用——放大了才看得清要框的那格。
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Button, Space, Tooltip } from 'antd'
import {
  AimOutlined, ColumnWidthOutlined, ZoomInOutlined, ZoomOutOutlined,
} from '@ant-design/icons'

const MIN_SCALE = 0.2
const MAX_SCALE = 12
const WHEEL_STEP = 1.15
const BUTTON_STEP = 1.3

interface ZoomPanViewerProps {
  children: ReactNode
  /** 视口高度(px) */
  height?: number
  /** 复位时的初始缩放 */
  initialScale?: number
  /** 缩放变化回调(标记层可据此调整标记大小) */
  onScaleChange?: (scale: number) => void
  /** 禁用拖拽平移(标注模式:拖拽要留给框选/画线) */
  panDisabled?: boolean
}

export default function ZoomPanViewer({
  children, height = 560, initialScale = 1, onScaleChange, panDisabled = false,
}: ZoomPanViewerProps) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(initialScale)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => { onScaleChange?.(scale) }, [scale, onScaleChange])

  const reset = useCallback(() => {
    setScale(initialScale)
    setOffset({ x: 0, y: 0 })
  }, [initialScale])

  /** 以指针为锚点缩放:锚点在图上的位置保持不动 */
  const zoomAt = useCallback((factor: number, clientX?: number, clientY?: number) => {
    const box = viewportRef.current?.getBoundingClientRect()
    setScale((prev) => {
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev * factor))
      if (box) {
        const px = (clientX ?? box.left + box.width / 2) - box.left
        const py = (clientY ?? box.top + box.height / 2) - box.top
        setOffset((o) => ({
          x: px - ((px - o.x) * next) / prev,
          y: py - ((py - o.y) * next) / prev,
        }))
      }
      return next
    })
  }, [])

  // 非被动监听才能 preventDefault,避免滚轮缩放时页面跟着滚
  useEffect(() => {
    const node = viewportRef.current
    if (!node) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomAt(e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP, e.clientX, e.clientY)
    }
    node.addEventListener('wheel', onWheel, { passive: false })
    return () => node.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  const onMouseDown = (e: React.MouseEvent) => {
    if (panDisabled) return
    dragRef.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y }
    setDragging(true)
  }
  const onMouseMove = (e: React.MouseEvent) => {
    const d = dragRef.current
    if (!d) return
    setOffset({ x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) })
  }
  const endDrag = () => { dragRef.current = null; setDragging(false) }

  return (
    <div>
      <Space size={4} style={{ marginBottom: 8 }}>
        <Tooltip title="放大(或滚轮上滚)">
          <Button size="small" icon={<ZoomInOutlined />} onClick={() => zoomAt(BUTTON_STEP)} />
        </Tooltip>
        <Tooltip title="缩小(或滚轮下滚)">
          <Button size="small" icon={<ZoomOutOutlined />} onClick={() => zoomAt(1 / BUTTON_STEP)} />
        </Tooltip>
        <Tooltip title="复位(或双击图纸)">
          <Button size="small" icon={<AimOutlined />} onClick={reset} />
        </Tooltip>
        <Tooltip title="适应宽度">
          <Button size="small" icon={<ColumnWidthOutlined />}
            onClick={() => { setScale(1); setOffset({ x: 0, y: 0 }) }} />
        </Tooltip>
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>
          {Math.round(scale * 100)}%
          {panDisabled ? ' · 标注中(滚轮仍可缩放)' : ' · 滚轮缩放 / 拖拽平移 / 双击复位'}
        </span>
      </Space>
      <div
        ref={viewportRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        onDoubleClick={panDisabled ? undefined : reset}
        style={{
          height, overflow: 'hidden', position: 'relative',
          background: '#f5f5f5', border: '1px solid #f0f0f0', borderRadius: 4,
          cursor: panDisabled ? 'crosshair' : (dragging ? 'grabbing' : 'grab'),
          userSelect: 'none',
        }}
      >
        <div
          style={{
            position: 'absolute', transformOrigin: '0 0',
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          }}
        >
          {children}
        </div>
      </div>
    </div>
  )
}
