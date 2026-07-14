/**
 * 近期活动时间线
 */
import { Card, Timeline, Typography } from 'antd'
import { ACTIVITY_ACTION_LABEL } from './constants'
import type { RecentActivityRow } from './types'

interface RecentActivityPanelProps {
  activities: RecentActivityRow[]
}

export default function RecentActivityPanel({ activities }: RecentActivityPanelProps) {
  if (activities.length === 0) {
    return (
      <Card title="近期活动">
        <Typography.Text type="secondary">暂无活动记录</Typography.Text>
      </Card>
    )
  }

  return (
    <Card title="近期活动">
      <Timeline
        items={activities.map((row, index) => ({
          key: `${row.action}-${row.resource_id}-${index}`,
          children: (
            <div>
              <Typography.Text strong style={{ fontSize: 13 }}>
                {ACTIVITY_ACTION_LABEL[row.action] ?? row.action}
              </Typography.Text>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {row.operator} · {new Date(row.created_at).toLocaleString('zh-CN')}
              </Typography.Text>
            </div>
          ),
        }))}
      />
    </Card>
  )
}
