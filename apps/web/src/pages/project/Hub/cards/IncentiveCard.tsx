import { useNavigate } from '@umijs/max'
import { Alert, Button, Card, Empty, Space, Statistic, Tag } from 'antd'
import { TrophyOutlined, WarningOutlined } from '@ant-design/icons'
import { PROPOSAL_STATUS_LABEL } from '../constants'
import type { ProposalFunnelRow } from '../types'

interface IncentiveCardProps {
  proposalFunnel: ProposalFunnelRow[]
  annualSavingYuan: number
  kpiRedFlag: boolean
}

export default function IncentiveCard({
  proposalFunnel,
  annualSavingYuan,
  kpiRedFlag,
}: IncentiveCardProps) {
  const navigate = useNavigate()
  const total = proposalFunnel.reduce((sum, row) => sum + Number(row.cnt), 0)

  return (
    <Card
      title={
        <Space>
          <TrophyOutlined />
          创效提案
        </Space>
      }
      extra={
        <Button size="small" onClick={() => navigate('/incentive')}>
          查看提案
        </Button>
      }
      style={{ height: '100%' }}
    >
      {kpiRedFlag && (
        <Alert
          type="error"
          showIcon
          icon={<WarningOutlined />}
          message="KPI 红线预警：年度创效额不足，将影响年度评优"
          style={{ marginBottom: 12 }}
        />
      )}
      {total === 0 ? (
        <Empty description="暂无创效提案" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <>
          <Statistic
            title="年度创效额"
            value={(annualSavingYuan / 10000).toFixed(1)}
            suffix="万元"
          />
          <Space wrap style={{ marginTop: 12 }}>
            {proposalFunnel.map((row) => (
              <Tag key={row.status}>
                {PROPOSAL_STATUS_LABEL[row.status] ?? row.status} {row.cnt}
              </Tag>
            ))}
          </Space>
        </>
      )}
    </Card>
  )
}
