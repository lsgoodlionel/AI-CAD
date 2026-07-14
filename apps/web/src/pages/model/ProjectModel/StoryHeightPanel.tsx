/**
 * 楼层标高校正（Task 3）：自动识别打底 → 人工录入校正。
 * 表格展示每层「自动识别层高(参考)」与「人工层高(可录入)」；保存后重建模型生效。
 */
import { Button, Empty, InputNumber, Space, Table, Tag, message } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import {
  getModelStoryHeights,
  saveModelStoryHeights,
  type StoryHeightRow,
} from '@/services/projectModel'

interface StoryHeightPanelProps {
  projectId: string
  /** 保存成功后回调（通常提示/触发重建） */
  onSaved?: () => void
}

interface EditState {
  height: number | null
  note: string
}

export default function StoryHeightPanel({ projectId, onSaved }: StoryHeightPanelProps): JSX.Element {
  const [rows, setRows] = useState<StoryHeightRow[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [edits, setEdits] = useState<Record<string, EditState>>({})

  const load = useMemo(
    () => async () => {
      setLoading(true)
      try {
        const res = await getModelStoryHeights(projectId)
        const data = res.data ?? []
        setRows(data)
        // 初始化编辑态：已有人工值则回填，否则留空（占位显示自动参考值）
        const init: Record<string, EditState> = {}
        data.forEach((r: StoryHeightRow) => {
          init[`${r.scope_key}:${r.story_key}`] = {
            height: r.manual_height_m,
            note: r.note ?? '',
          }
        })
        setEdits(init)
      } catch {
        message.error('读取楼层标高失败')
      } finally {
        setLoading(false)
      }
    },
    [projectId],
  )

  useEffect(() => {
    void load()
  }, [load])

  const keyOf = (r: StoryHeightRow) => `${r.scope_key}:${r.story_key}`

  const handleSave = async () => {
    setSaving(true)
    try {
      const items = rows.map((r) => {
        const edit = edits[keyOf(r)]
        return {
          scope_key: r.scope_key,
          story_key: r.story_key,
          story_order: r.story_order,
          height_m: edit?.height ?? null, // null/≤0 → 清除该录入，恢复自动
          note: edit?.note || null,
        }
      })
      const res = await saveModelStoryHeights(projectId, items)
      message.success(`已保存 ${res.data?.saved ?? 0} 条，重建模型后生效`)
      onSaved?.()
      void load()
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const columns = [
    {
      title: '楼层',
      dataIndex: 'story_label',
      width: 90,
      render: (v: string, r: StoryHeightRow) =>
        r.manual_height_m != null ? (
          <Space size={4}>
            {v}
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>人工</Tag>
          </Space>
        ) : (
          v
        ),
    },
    {
      title: '自动层高(m)',
      dataIndex: 'auto_height_m',
      width: 96,
      render: (v: number | null) => (
        <span style={{ color: '#8c8c8c' }}>{v == null ? '—' : v.toFixed(2)}</span>
      ),
    },
    {
      title: '人工层高(m)',
      width: 120,
      render: (_: unknown, r: StoryHeightRow) => (
        <InputNumber
          size="small"
          min={0}
          step={0.1}
          style={{ width: 96 }}
          placeholder={r.auto_height_m == null ? '录入' : r.auto_height_m.toFixed(2)}
          value={edits[keyOf(r)]?.height ?? undefined}
          onChange={(val) =>
            setEdits((prev) => ({
              ...prev,
              [keyOf(r)]: { height: (val as number) ?? null, note: prev[keyOf(r)]?.note ?? '' },
            }))
          }
        />
      ),
    },
    {
      title: '标高(m)',
      dataIndex: 'auto_elevation_m',
      width: 84,
      render: (v: number | null) => (v == null ? '—' : v.toFixed(2)),
    },
  ]

  if (!loading && rows.length === 0) {
    return <Empty description="暂无楼层，请先生成模型" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 8 }}>
        自动识别层高为参考值(灰色)。按剖面/设计说明在「人工层高」录入真实值校正，
        留空则沿用自动值。保存后<b>重建模型</b>生效。
      </div>
      <Table
        size="small"
        rowKey={keyOf}
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={false}
        scroll={{ y: 300 }}
      />
      <div style={{ marginTop: 10, textAlign: 'right' }}>
        <Button type="primary" size="small" loading={saving} onClick={handleSave}>
          保存层高校正
        </Button>
      </div>
    </div>
  )
}
