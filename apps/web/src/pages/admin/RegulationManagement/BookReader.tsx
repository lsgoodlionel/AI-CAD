/**
 * 规范「人机双读」三层阅读器。
 *
 * 需求：规范库要**同时满足机器和人类阅读** ——
 * ① 可阅读的 **PDF 原件**（人看原版排版、图表、签章）
 * ② 被识别的**文字版本全文**（人核对识别质量）
 * ③ 整理处理过的**单个条文**（系统消费）
 *
 * 此前界面只有第③层，而且**看不出另外两层是缺的**：
 * 31 本书的 `file_key` 全为 NULL、全文从未保留。
 * 所以这个组件第一件事就是把三层的就绪状态摆出来。
 */
import { useEffect, useState } from 'react'
import { Alert, Descriptions, Empty, Modal, Skeleton, Space, Tabs, Tag, Typography } from 'antd'

import { getBookLayers, getBookText, previewBook } from '@/services/regulations'
import type { BookLayers } from '@/services/regulations'

const { Paragraph, Text } = Typography

/** 文字来路 → 人能懂的说法。**这不是装饰**：OCR 出来的和 PDF 文本层
 *  直取的可信度完全不同，不知道来路就没法判断该信到什么程度。 */
const METHOD_LABEL: Record<string, string> = {
  text_layer: 'PDF 文本层直取（保真）',
  ocr: 'OCR 识别（扫描件，需人工核对）',
  docling: '版面解析（表格保真更好）',
}

/** 单次拉取的字数。整本规范全文可达数十万字，一次塞给浏览器不合适。 */
const TEXT_PAGE_CHARS = 20000

export default function BookReader({
  bookId,
  open,
  onClose,
}: {
  bookId: string | null
  open: boolean
  onClose: () => void
}) {
  const [layers, setLayers] = useState<BookLayers | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [pdfReason, setPdfReason] = useState<string>('')
  const [text, setText] = useState('')
  const [textMeta, setTextMeta] = useState<{ method: string | null; chars: number } | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !bookId) return
    setLoading(true)
    setText('')
    setPdfUrl(null)
    Promise.all([
      getBookLayers(bookId).catch(() => null),
      previewBook(bookId).catch(() => null),
      getBookText(bookId, 0, TEXT_PAGE_CHARS).catch(() => null),
    ])
      .then(([l, p, t]) => {
        setLayers(l)
        setPdfUrl(p?.kind === 'pdf' ? p.url : null)
        setPdfReason(p?.reason ?? '')
        if (t) {
          setText(t.text)
          setTextMeta({ method: t.extract_method, chars: t.text_chars })
        }
      })
      .finally(() => setLoading(false))
  }, [bookId, open])

  const loadMore = async () => {
    if (!bookId) return
    const next = await getBookText(bookId, text.length, TEXT_PAGE_CHARS)
    setText((prev) => prev + next.text)
  }

  const readiness = layers && (
    <Descriptions size="small" column={3} bordered style={{ marginBottom: 12 }}>
      <Descriptions.Item label="① PDF 原件">
        {layers.pdf.ready ? (
          <Tag color="green">{layers.pdf.page_count ?? '?'} 页</Tag>
        ) : (
          <Tag>未保存</Tag>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="② 识别全文">
        {layers.full_text.ready ? (
          <Tag color="green">{layers.full_text.chars} 字</Tag>
        ) : (
          <Tag>未保留</Tag>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="③ 单条条文">
        {layers.articles.ready ? (
          <Space size={4}>
            <Tag color="green">{layers.articles.total} 条</Tag>
            <Tag color="red">强条 {layers.articles.mandatory}</Tag>
            <Tag color={layers.articles.graphed ? 'blue' : 'default'}>
              图谱 {layers.articles.graphed}
            </Tag>
            <Tag color={layers.articles.vectored ? 'purple' : 'default'}>
              向量 {layers.articles.vectored}
            </Tag>
          </Space>
        ) : (
          <Tag>未解析</Tag>
        )}
      </Descriptions.Item>
    </Descriptions>
  )

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width="90%"
      style={{ top: 24 }}
      title={layers ? `${layers.std_no ?? ''} ${layers.title}` : '规范阅读'}
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : (
        <>
          {readiness}
          <Tabs
            items={[
              {
                key: 'pdf',
                label: '① PDF 原件',
                children: pdfUrl ? (
                  <iframe
                    title="规范原件"
                    src={pdfUrl}
                    style={{ width: '100%', height: '68vh', border: '1px solid #f0f0f0' }}
                  />
                ) : (
                  <Empty description={pdfReason || '该规范未保存原件'} />
                ),
              },
              {
                key: 'text',
                label: '② 识别全文',
                children: text ? (
                  <>
                    <Alert
                      type={textMeta?.method === 'ocr' ? 'warning' : 'info'}
                      showIcon
                      style={{ marginBottom: 8 }}
                      message={`文字来路：${
                        METHOD_LABEL[textMeta?.method ?? ''] ?? '未知'
                      }`}
                      description="与左侧 PDF 原件逐段对照，可发现识别错漏；条文层就是从这份文字切出来的。"
                    />
                    <Paragraph
                      style={{
                        whiteSpace: 'pre-wrap',
                        maxHeight: '60vh',
                        overflow: 'auto',
                        background: '#fafafa',
                        padding: 12,
                        marginBottom: 0,
                      }}
                    >
                      {text}
                    </Paragraph>
                    {textMeta && text.length < textMeta.chars && (
                      <Text
                        style={{ cursor: 'pointer' }}
                        type="secondary"
                        onClick={loadMore}
                      >
                        已显示 {text.length} / {textMeta.chars} 字 —— 点击继续加载
                      </Text>
                    )}
                  </>
                ) : (
                  <Empty description="该规范未保留识别全文，重新导入即可生成" />
                ),
              },
            ]}
          />
        </>
      )}
    </Modal>
  )
}
