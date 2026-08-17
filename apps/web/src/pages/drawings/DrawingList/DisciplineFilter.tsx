/**
 * 专业筛选栏 —— 图纸列表按专业一键切换。
 *
 * 专业筛选本来只藏在 ProTable 可折叠搜索表单里(要展开、选下拉、点查询),
 * 而按专业看图是最高频的动作。这里做成常驻的一行按钮 + 每专业张数,一点即筛。
 *
 * 专业取值以**图框「专业」栏实读值**为准(给排水/基坑围护/电气…,见
 * `services/title_block_discipline.py`);图框读不到的回落粗粒度枚举并标注,
 * 让「哪些图还没读到专业」一眼可见,而不是悄悄归入某个大类。
 */
import { useEffect, useState } from 'react'
import { Space, Tag, Typography } from 'antd'
import { getDisciplineSummary, type DisciplineBucket } from '@/services/drawings'
import { DISCIPLINE_LABEL } from './UploadWizard'

const { Text } = Typography

interface DisciplineFilterProps {
  /** 当前选中的专业;空串 = 全部 */
  value: string
  onChange: (discipline: string) => void
  /** 限定项目时计数随之收敛 */
  projectId?: string
}

export default function DisciplineFilter({
  value, onChange, projectId,
}: DisciplineFilterProps) {
  const [buckets, setBuckets] = useState<DisciplineBucket[]>([])
  const [total, setTotal] = useState(0)

  useEffect(() => {
    getDisciplineSummary(projectId ? { project_id: projectId } : undefined)
      .then((res) => { setBuckets(res.items); setTotal(res.total) })
      .catch(() => { setBuckets([]); setTotal(0) })   // 计数拿不到不影响筛选可用
  }, [projectId])

  return (
    <Space wrap size={4} style={{ marginBottom: 12 }}>
      <Text type="secondary" style={{ fontSize: 12, marginRight: 4 }}>专业</Text>
      <Tag.CheckableTag checked={!value} onChange={() => onChange('')}>
        全部{total ? ` ${total}` : ''}
      </Tag.CheckableTag>
      {buckets.map((b) => (
        <Tag.CheckableTag
          key={b.key}
          checked={value === b.key}
          onChange={() => onChange(value === b.key ? '' : b.key)}
        >
          {b.is_detail ? b.key : `${DISCIPLINE_LABEL[b.key] ?? b.key}(未读到图框)`} {b.count}
        </Tag.CheckableTag>
      ))}
    </Space>
  )
}
