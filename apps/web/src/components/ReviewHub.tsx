/**
 * 人审工作台 —— 统一入口(解决「找不到方便入口做审核修正复核」)。
 *
 * 此前人审入口散落 6 处:比例尺确认在工程信息页、构件核对在模型页审校模式、
 * 楼层标高在另一面板、追溯在预览弹窗、回投在构件抽屉…… 本组件把全部待办聚成
 * 一张清单:**数量 + 为什么值得做 + 一键直达**,并按价值排序(先做见效最快的)。
 */
import { useEffect, useState } from 'react'
import { history } from '@umijs/max'
import { Badge, Button, Card, Empty, Space, Spin, Tag, Typography } from 'antd'
import { RightOutlined } from '@ant-design/icons'
import { getReviewTasks, type ReviewTask } from '@/services/projectInfo'

const { Text } = Typography

const SEVERITY_META: Record<string, { color: string; label: string }> = {
  high: { color: 'red', label: '优先' },
  medium: { color: 'orange', label: '常规' },
  low: { color: 'default', label: '可选' },
}

interface ReviewHubProps {
  projectId: string
  /** 紧凑模式:只显示有待办的项 */
  compact?: boolean
}

export default function ReviewHub({ projectId, compact = false }: ReviewHubProps) {
  const [tasks, setTasks] = useState<ReviewTask[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    getReviewTasks(projectId)
      .then((res) => {
        if (!alive) return
        setTasks(res.tasks)
        setTotal(res.total_pending)
      })
      .catch(() => alive && setTasks([]))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [projectId])

  const shown = compact ? tasks.filter((t) => t.count > 0) : tasks

  return (
    <Card
      size="small"
      title={
        <Space>
          <span>人审工作台</span>
          {total > 0 ? <Badge count={total} overflowCount={99999} /> : null}
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 24 }}><Spin size="small" /></div>
      ) : shown.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待办" />
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 10,
        }}>
          {shown.map((task) => {
            const meta = SEVERITY_META[task.severity] ?? SEVERITY_META.low
            return (
              <div
                key={task.key}
                style={{
                  border: '1px solid #f0f0f0', borderRadius: 8, padding: '10px 12px',
                  background: task.count > 0 ? '#fff' : '#fafafa',
                }}
              >
                <Space style={{ marginBottom: 4 }} wrap size={4}>
                  <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.label}</Tag>
                  <Text strong>{task.title}</Text>
                  <Text type={task.count > 0 ? 'danger' : 'secondary'}>{task.count}</Text>
                </Space>
                <div style={{ fontSize: 12, color: '#8c8c8c', minHeight: 32, lineHeight: 1.5 }}>
                  {task.why}
                </div>
                <Button
                  size="small" type="link" style={{ padding: 0, marginTop: 4 }}
                  disabled={task.count === 0}
                  onClick={() => history.push(task.route)}
                >
                  去处理 <RightOutlined />
                </Button>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
