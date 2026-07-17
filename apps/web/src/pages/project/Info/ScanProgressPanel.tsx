/**
 * 扫描进度面板(Phase F3)
 *
 * 精确到每张图纸的各类信息读取进度 + 内容摘要,实时轮询(默认 4s):
 * - 顶部总进度条(ready/total)+ 状态计数
 * - 逐图表格:状态 / 已完成抽取器(矢量·OCR·VLM)/ 各类 token 数 / 内容样例
 * - 「开始全量扫描(含 VLM)」按钮
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Badge, Button, Card, Progress, Space, Table, Tag, Tooltip, Typography, message,
} from 'antd'
import { ReloadOutlined, ScanOutlined } from '@ant-design/icons'
import {
  getScanProgress,
  triggerInfoExtract,
  INFO_CATEGORY_LABEL,
  INFO_EXTRACTOR_LABEL,
} from '@/services/projectInfo'
import type { ScanDrawing, ScanProgress } from '@/services/projectInfo'

const { Text } = Typography

const POLL_MS = 4000

const STATUS_META: Record<string, { badge: 'success' | 'processing' | 'default'; text: string }> = {
  ready: { badge: 'success', text: '完成' },
  extracting: { badge: 'processing', text: '扫描中' },
  pending: { badge: 'default', text: '待扫描' },
}

const EXTRACTOR_COLOR: Record<string, string> = {
  vector_text: 'blue',
  ocr: 'green',
  vlm: 'purple',
  filename: 'default',
}

export default function ScanProgressPanel({ projectId }: { projectId: string }) {
  const [data, setData] = useState<ScanProgress | null>(null)
  const [starting, setStarting] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval>>()

  const load = useCallback(() => {
    getScanProgress(projectId, { page_size: 100 })
      .then(setData)
      .catch(() => {})
  }, [projectId])

  useEffect(() => {
    load()
    timer.current = setInterval(load, POLL_MS)
    return () => clearInterval(timer.current)
  }, [load])

  const startScan = async () => {
    setStarting(true)
    try {
      await triggerInfoExtract(projectId, true)
      message.success('全量扫描已启动(含 VLM,较慢),进度将实时刷新')
      load()
    } catch {
      message.error('启动扫描失败')
    } finally {
      setStarting(false)
    }
  }

  const overall = data?.overall
  const percent = overall?.percent ?? 0

  const columns = [
    {
      title: '图纸',
      dataIndex: 'drawing_no',
      width: 200,
      render: (_: unknown, r: ScanDrawing) => (
        <Tooltip title={r.title}>
          <Text style={{ fontSize: 12 }}>{r.drawing_no || r.title.slice(0, 18)}</Text>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => {
        const m = STATUS_META[v] ?? STATUS_META.pending
        return <Badge status={m.badge} text={m.text} />
      },
    },
    {
      title: '已读取(抽取器)',
      dataIndex: 'extractors_done',
      width: 200,
      render: (exts: string[], r: ScanDrawing) => (
        <Space size={2} wrap>
          {(exts ?? []).map((e) => {
            const n = r.summary?.by_extractor?.[e]
            return (
              <Tag key={e} color={EXTRACTOR_COLOR[e] ?? 'default'} style={{ marginInlineEnd: 2 }}>
                {INFO_EXTRACTOR_LABEL[e] ?? e}{n != null ? ` ${n}` : ''}
              </Tag>
            )
          })}
        </Space>
      ),
    },
    {
      title: '各类信息',
      dataIndex: 'summary',
      render: (s: ScanDrawing['summary']) => {
        const byCat = s?.by_category ?? {}
        const entries = Object.entries(byCat).sort((a, b) => b[1] - a[1])
        if (!entries.length) return <Text type="secondary">—</Text>
        return (
          <Space size={4} wrap>
            {entries.map(([cat, n]) => (
              <Text key={cat} style={{ fontSize: 12 }}>
                {INFO_CATEGORY_LABEL[cat] ?? cat}
                <Text type="secondary">·{n}</Text>
              </Text>
            ))}
          </Space>
        )
      },
    },
    {
      title: '内容摘要',
      dataIndex: 'summary',
      width: 260,
      render: (s: ScanDrawing['summary']) => {
        const samples = s?.samples ?? []
        if (!samples.length) return <Text type="secondary">—</Text>
        return (
          <Text style={{ fontSize: 12 }} type="secondary" ellipsis>
            {samples.map((x) => x.text).join(' · ')}
          </Text>
        )
      },
    },
  ]

  return (
    <Card
      size="small"
      title={<Space><ScanOutlined />扫描进度(OCR · 矢量 · VLM)</Space>}
      extra={
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button
            size="small" type="primary" icon={<ScanOutlined />}
            loading={starting} onClick={startScan}
          >
            开始全量扫描(含 VLM)
          </Button>
        </Space>
      }
    >
      {overall ? (
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Space size="large" wrap>
            <Progress
              percent={percent}
              style={{ width: 280 }}
              status={overall.extracting > 0 ? 'active' : undefined}
            />
            <Text>
              完成 <Text strong>{overall.ready}</Text> / {overall.total} ·
              扫描中 <Text type="warning">{overall.extracting}</Text> ·
              待扫描 {overall.pending}
            </Text>
          </Space>
          <Table<ScanDrawing>
            rowKey="drawing_id"
            size="small"
            columns={columns}
            dataSource={data?.drawings ?? []}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            scroll={{ y: 420 }}
          />
        </Space>
      ) : (
        <Text type="secondary">加载扫描进度…</Text>
      )}
    </Card>
  )
}
