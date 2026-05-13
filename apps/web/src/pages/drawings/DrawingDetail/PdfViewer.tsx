import { useState } from 'react'
import { Viewer, Worker } from '@react-pdf-viewer/core'
import { defaultLayoutPlugin } from '@react-pdf-viewer/default-layout'
import { Alert, Spin } from 'antd'

import '@react-pdf-viewer/core/lib/styles/index.css'
import '@react-pdf-viewer/default-layout/lib/styles/index.css'

interface PdfViewerProps {
  url: string
  height?: number
}

const PDFJS_CDN = 'https://unpkg.com/pdfjs-dist@3.11.174/build/pdf.worker.min.js'

export default function PdfViewer({ url, height = 680 }: PdfViewerProps) {
  const [loadError, setLoadError] = useState<string | null>(null)
  const defaultLayout = defaultLayoutPlugin()

  if (loadError) {
    return (
      <Alert
        type="error"
        message="PDF 加载失败"
        description={loadError}
        showIcon
        style={{ margin: '12px 0' }}
      />
    )
  }

  return (
    <Worker workerUrl={PDFJS_CDN}>
      <div style={{ height, border: '1px solid #d9d9d9', borderRadius: 8, overflow: 'hidden' }}>
        <Viewer
          fileUrl={url}
          plugins={[defaultLayout]}
          onDocumentLoadError={(err) => setLoadError(err.message)}
          renderLoader={(percentages) => (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
              <Spin tip={`加载中 ${Math.round(percentages)}%`} />
            </div>
          )}
        />
      </div>
    </Worker>
  )
}
