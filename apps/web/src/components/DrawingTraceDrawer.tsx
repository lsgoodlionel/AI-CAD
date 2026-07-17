/**
 * 图纸追溯抽屉(Phase G2)—— 正向追溯:这张图识别了什么 + 用在哪
 *
 * 全站复用:预览弹窗、图纸列表、工程信息页均可一键打开。
 * 内容:①识别信息按类别/抽取器汇总 ②模型用途(生成的构件按楼层/类别 + 模型版本)
 */
import { useEffect, useState } from 'react'
import { Drawer, Empty, Spin, Statistic, Table, Tag, Typography, Space, Alert, message } from 'antd'
import { getDrawingTrace, INFO_CATEGORY_LABEL, INFO_EXTRACTOR_LABEL, ELEMENT_KIND_LABEL } from '@/services/projectInfo'
import type { DrawingTrace } from '@/services/projectInfo'

const { Text, Title } = Typography

interface DrawingTraceDrawerProps {
  drawingId: string | null
  onClose: () => void
}

const EXTRACTOR_COLOR: Record<string, string> = {
  vector_text: 'blue', ocr: 'green', vlm: 'purple', filename: 'default',
}

export default function DrawingTraceDrawer({ drawingId, onClose }: DrawingTraceDrawerProps) {
  const [loading, setLoading] = useState(false)
  const [trace, setTrace] = useState<DrawingTrace | null>(null)

  useEffect(() => {
    if (!drawingId) return
    setLoading(true)
    setTrace(null)
    getDrawingTrace(drawingId)
      .then(setTrace)
      .catch(() => message.error('追溯信息加载失败'))
      .finally(() => setLoading(false))
  }, [drawingId])

  const info = trace?.info
  const usage = trace?.model_usage

  const catRows = Object.entries(info?.by_category ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ key: k, label: INFO_CATEGORY_LABEL[k] ?? k, count: v }))

  return (
    <Drawer
      open={!!drawingId}
      title={trace ? `追溯 · ${trace.drawing.drawing_no || trace.drawing.title}` : '图纸追溯'}
      width={520}
      onClose={onClose}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin tip="加载追溯…" /></div>
      ) : trace ? (
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          {/* 概览 */}
          <Space size="large">
            <Statistic title="识别信息条数" value={info?.total ?? 0} />
            <Statistic title="生成模型构件" value={usage?.total_elements ?? 0} />
          </Space>

          {/* 识别信息:按类别 */}
          <div>
            <Title level={5}>① 识别出的信息(按类别)</Title>
            <Space wrap size={4} style={{ marginBottom: 8 }}>
              {Object.entries(info?.by_extractor ?? {}).map(([e, n]) => (
                <Tag key={e} color={EXTRACTOR_COLOR[e] ?? 'default'}>
                  {INFO_EXTRACTOR_LABEL[e] ?? e} {n}
                </Tag>
              ))}
            </Space>
            <Table
              size="small"
              rowKey="key"
              pagination={false}
              columns={[
                { title: '类别', dataIndex: 'label' },
                { title: '条数', dataIndex: 'count', width: 80, align: 'right' as const },
              ]}
              dataSource={catRows}
            />
          </div>

          {/* 模型用途 */}
          <div>
            <Title level={5}>② 用在哪(生成的模型构件)</Title>
            {usage?.used ? (
              <>
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 8 }}
                  message={`已用于工程模型(v${usage.model_version ?? '-'}),共生成 ${usage.total_elements} 个构件`}
                />
                <Table
                  size="small"
                  rowKey="key"
                  pagination={false}
                  columns={[
                    { title: '楼层', dataIndex: 'label' },
                    {
                      title: '生成构件',
                      dataIndex: 'by_kind',
                      render: (byKind: Record<string, number>) => (
                        <Space wrap size={4}>
                          {Object.entries(byKind).map(([k, n]) => (
                            <Text key={k} style={{ fontSize: 12 }}>
                              {ELEMENT_KIND_LABEL[k] ?? k}
                              <Text type="secondary">·{n}</Text>
                            </Text>
                          ))}
                        </Space>
                      ),
                    },
                    { title: '小计', dataIndex: 'count', width: 60, align: 'right' as const },
                  ]}
                  dataSource={usage.floors.map((f) => ({ ...f, key: f.key }))}
                />
              </>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="该图未生成模型构件(可能是说明/详图/未分层图,或建模未覆盖)"
              />
            )}
          </div>
        </Space>
      ) : null}
    </Drawer>
  )
}
