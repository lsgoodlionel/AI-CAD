import { Alert } from 'antd'

interface PdfViewerProps {
  url: string
  height?: number
}

export default function PdfViewer({ url, height = 680 }: PdfViewerProps) {
  if (!url) {
    return (
      <Alert
        type="error"
        message="PDF 地址为空"
        showIcon
        style={{ margin: '12px 0' }}
      />
    )
  }

  return (
    <iframe
      title="PDF 预览"
      src={url}
      style={{
        width: '100%',
        height,
        border: '1px solid #d9d9d9',
        borderRadius: 8,
        background: '#fff',
      }}
    />
  )
}
