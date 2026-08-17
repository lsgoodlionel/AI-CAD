/**
 * H4+ 回投核对 —— 把装配构件叠加到图纸预览上,让人在**图纸本身**上核对位置。
 * 标记 x/y 为后端归一化(同除 page_h),像素 = 归一化值 × 图片显示高度。
 * 仅支持图片预览(PDF 预览的坐标叠加较复杂,暂降级为提示)。坐标依赖 drawing_transform,
 * 无变换的图后端返回 available=false。视觉对齐需真实图纸校验(数学已验证)。
 */
import { useEffect, useRef, useState } from 'react'
import { Alert, Modal, Spin, Tag } from 'antd'
import { getDrawingPreview, type DrawingPreview } from '@/services/projectInfo'
import ZoomPanViewer from '@/components/ZoomPanViewer'
import { getComponentsOverlay, type OverlayMarker } from '@/services/projectModel'

const STATE_COLOR: Record<string, string> = {
  confirmed: '#52c41a', conflict: '#faad14', auto: '#1890ff', rejected: '#bfbfbf',
}

interface DrawingOverlayProps {
  projectId: string
  drawingId: string | null
  onClose: () => void
}

export default function DrawingOverlay({ projectId, drawingId, onClose }: DrawingOverlayProps) {
  const [preview, setPreview] = useState<DrawingPreview | null>(null)
  const [markers, setMarkers] = useState<OverlayMarker[]>([])
  const [available, setAvailable] = useState(true)
  const [loading, setLoading] = useState(false)
  const [imgH, setImgH] = useState(0)
  const [viewScale, setViewScale] = useState(1)
  const imgRef = useRef<HTMLImageElement>(null)

  useEffect(() => {
    if (!drawingId) return
    setLoading(true); setPreview(null); setMarkers([]); setImgH(0)
    Promise.all([
      getDrawingPreview(drawingId).catch(() => null),
      getComponentsOverlay(projectId, drawingId).catch(() => ({ available: false, markers: [] })),
    ])
      .then(([pv, ov]) => { setPreview(pv); setMarkers(ov.markers); setAvailable(ov.available) })
      .finally(() => setLoading(false))
  }, [projectId, drawingId])

  const onImgLoad = () => { if (imgRef.current) setImgH(imgRef.current.clientHeight) }

  return (
    <Modal
      open={!!drawingId}
      title="在图纸上核对构件位置"
      width={900}
      footer={null}
      onCancel={onClose}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin tip="加载图纸与构件…" /></div>
      ) : !available ? (
        <Alert type="info" showIcon message="该图纸无坐标变换,无法回投构件位置(需 drawing_transform)" />
      ) : preview?.kind !== 'image' ? (
        <Alert type="info" showIcon
          message="当前预览为 PDF,构件叠加暂仅支持图片预览" />
      ) : (
        <>
          <div style={{ marginBottom: 8 }}>
            <Tag color="#1890ff">auto</Tag><Tag color="#faad14">待人审</Tag>
            <Tag color="#52c41a">已确认</Tag>
            <span style={{ fontSize: 12, color: '#888' }}>共 {markers.length} 个构件</span>
          </div>
          {/* 缩放平移:核对构件位置必须能放大到看清图纸细节 */}
          <ZoomPanViewer height={520} onScaleChange={setViewScale}>
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <img
                ref={imgRef} src={preview.url} alt="drawing" onLoad={onImgLoad}
                draggable={false}
                style={{ display: 'block', maxWidth: 'none' }}
              />
              {imgH > 0 && markers.map((m) => {
                // 标记随图缩放会变形,故按 1/scale 反向补偿 → 视觉大小恒定
                const size = 7 / viewScale
                return (
                  <span
                    key={m.id}
                    title={`${m.type} · ${m.review_state}`}
                    style={{
                      position: 'absolute',
                      left: m.x * imgH - size / 2,
                      top: m.y * imgH - size / 2,
                      width: size, height: size, borderRadius: '50%',
                      background: STATE_COLOR[m.review_state] ?? '#1890ff',
                      boxShadow: `0 0 0 ${1 / viewScale}px #fff`,
                      pointerEvents: 'none',
                    }}
                  />
                )
              })}
            </div>
          </ZoomPanViewer>
        </>
      )}
    </Modal>
  )
}
