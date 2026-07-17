/**
 * 图纸统一预览弹窗(Phase E1-4,全站复用)
 *
 * 按后端 GET /drawings/{id}/preview 的 kind 分流:
 * - pdf   → iframe 内嵌(浏览器原生渲染)
 * - image → <img>(DXF/DWG 服务端渲染 PNG,或扫描图)
 * - 422/失败 → 提示 + 下载兜底
 *
 * 用法:<DrawingPreviewModal drawingId={id} title={t} onClose={...} />
 * drawingId 为 null 时不渲染。
 */
import { useEffect, useState } from 'react'
import { Alert, Button, Modal, Spin } from 'antd'
import { DownloadOutlined, NodeIndexOutlined } from '@ant-design/icons'
import { getDownloadUrl } from '@/services/drawings'
import { getDrawingPreview } from '@/services/projectInfo'
import type { DrawingPreview } from '@/services/projectInfo'
import DrawingTraceDrawer from './DrawingTraceDrawer'

interface DrawingPreviewModalProps {
  drawingId: string | null
  title?: string
  onClose: () => void
}

const PREVIEW_HEIGHT = 640

export default function DrawingPreviewModal({
  drawingId,
  title,
  onClose,
}: DrawingPreviewModalProps) {
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState<DrawingPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [traceOpen, setTraceOpen] = useState(false)

  useEffect(() => {
    if (!drawingId) return
    setLoading(true)
    setPreview(null)
    setError(null)
    getDrawingPreview(drawingId)
      .then(setPreview)
      .catch(() => setError('该图纸暂不支持在线预览，可下载后本地查看'))
      .finally(() => setLoading(false))
  }, [drawingId])

  const handleDownload = async () => {
    if (!drawingId) return
    try {
      const { url } = await getDownloadUrl(drawingId)
      window.open(url)
    } catch {
      setError('获取下载链接失败')
    }
  }

  return (
    <Modal
      open={!!drawingId}
      title={title || '图纸预览'}
      onCancel={onClose}
      width="80%"
      footer={[
        <Button
          key="trace"
          icon={<NodeIndexOutlined />}
          onClick={() => setTraceOpen(true)}
        >
          识别信息 / 用途追溯
        </Button>,
        <Button key="download" icon={<DownloadOutlined />} onClick={handleDownload}>
          下载原图
        </Button>,
        <Button key="close" type="primary" onClick={onClose}>
          关闭
        </Button>,
      ]}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin tip="加载预览…（CAD 图首次预览需服务端渲染，稍候）" />
        </div>
      ) : error ? (
        <Alert type="warning" message={error} showIcon />
      ) : preview?.kind === 'pdf' ? (
        <iframe
          src={preview.url}
          title="drawing-preview"
          style={{ width: '100%', height: PREVIEW_HEIGHT, border: 'none' }}
        />
      ) : preview?.kind === 'image' ? (
        <div style={{ maxHeight: PREVIEW_HEIGHT, overflow: 'auto', textAlign: 'center' }}>
          <img
            src={preview.url}
            alt="图纸预览"
            style={{ maxWidth: '100%' }}
          />
        </div>
      ) : null}

      <DrawingTraceDrawer
        drawingId={traceOpen ? drawingId : null}
        onClose={() => setTraceOpen(false)}
      />
    </Modal>
  )
}
