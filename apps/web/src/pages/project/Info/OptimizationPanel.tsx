/**
 * 系统自学习中心 —— 每次人工标注都反哺系统,下次识别更准更全。
 *
 * 闭环:**标注 → 分析 → 建议 → 人审采纳 → 生效**。
 *
 * 界面上刻意把两类建议分开显示,因为它们的含义完全不同:
 * - **可自动生效**:采纳即写入规则,下一轮识别当场受益(词表/OCR 纠错/阈值);
 * - **需开发介入**:采纳只代表「确认要做」,**系统行为不会改变**,导出交开发。
 *   混在一起会让人误以为点了采纳问题就解决了。
 *
 * 过程日志不是装饰:人要靠它判断「这条建议凭什么」,所以逐步展开可见。
 */
import { useEffect, useState } from 'react'
import {
  Alert, Badge, Button, Card, Descriptions, Empty, List, Space, Spin, Table,
  Tabs, Tag, Tooltip, Typography, message,
} from 'antd'
import { ExperimentOutlined, ThunderboltOutlined } from '@ant-design/icons'
import {
  exportOptimizationPackage, listLearnedRules, listOptimizationRuns,
  listSuggestions, reviewSuggestion, runOptimization,
  type ImprovementSuggestion, type LearnedRule, type OptimizationRun,
} from '@/services/projectInfo'

const { Paragraph, Text } = Typography

const CATEGORY_LABEL: Record<string, string> = {
  vocabulary: '词表扩充',
  ocr_correction: 'OCR 纠错',
  threshold: '参数阈值',
  template: '区域模板',
  algorithm: '算法改造',
}

const STATUS_TAG: Record<string, { color: string; text: string }> = {
  pending: { color: 'blue', text: '待处理' },
  accepted: { color: 'green', text: '已采纳并生效' },
  rejected: { color: 'default', text: '已否决' },
  exported: { color: 'orange', text: '待开发处理' },
}

interface OptimizationPanelProps {
  projectId: string
}

export default function OptimizationPanel({ projectId }: OptimizationPanelProps) {
  const [runs, setRuns] = useState<OptimizationRun[]>([])
  const [suggestions, setSuggestions] = useState<ImprovementSuggestion[]>([])
  const [rules, setRules] = useState<LearnedRule[]>([])
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)

  const load = () => {
    setLoading(true)
    Promise.all([
      listOptimizationRuns(projectId).then((r) => setRuns(r.items)),
      listSuggestions(projectId).then((r) => setSuggestions(r.items)),
      listLearnedRules(projectId).then((r) => setRules(r.items)),
    ])
      .catch(() => message.error('自学习数据加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [projectId])

  const analyze = async () => {
    setRunning(true)
    try {
      const { data } = await runOptimization(projectId)
      message.success(`已扫描 ${data.scanned} 条标注,产出 ${data.findings} 条建议`)
      load()
    } catch {
      message.error('学习分析失败')
    } finally {
      setRunning(false)
    }
  }

  const review = async (s: ImprovementSuggestion, accept: boolean) => {
    try {
      const { data } = await reviewSuggestion(projectId, s.id, accept)
      if (data.applied) {
        message.success('已采纳,规则当场生效,下一轮识别即受益')
      } else {
        message.info(data.note || (accept ? '已标记为待开发处理' : '已否决'))
      }
      load()
    } catch {
      message.error('提交失败')
    }
  }

  const exportPack = async () => {
    try {
      const pack = await exportOptimizationPackage(projectId)
      const blob = new Blob([JSON.stringify(pack, null, 2)],
        { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `optimization-${projectId}.json`
      a.click()
      URL.revokeObjectURL(url)
      message.success('已导出,可直接提交开发')
    } catch {
      message.error('导出失败')
    }
  }

  const pending = suggestions.filter((s) => s.status === 'pending')
  const autoOnes = pending.filter((s) => s.auto_applicable)
  const devOnes = pending.filter((s) => !s.auto_applicable)

  const renderSuggestions = (items: ImprovementSuggestion[], auto: boolean) => (
    <List
      dataSource={items}
      locale={{ emptyText: auto ? '暂无可自动生效的建议' : '暂无需开发处理的建议' }}
      renderItem={(s) => (
        <List.Item
          actions={[
            <Button key="ok" size="small" type="primary" onClick={() => review(s, true)}>
              {auto ? '采纳并生效' : '确认需开发'}
            </Button>,
            <Button key="no" size="small" onClick={() => review(s, false)}>否决</Button>,
          ]}
        >
          <List.Item.Meta
            title={
              <Space wrap>
                <Text strong>{s.title}</Text>
                <Tag>{CATEGORY_LABEL[s.category] ?? s.category}</Tag>
                <Tag color="blue">影响 {s.impact} 张</Tag>
                <Tooltip title="证据越多置信越高;低置信的建议不会自动提出">
                  <Tag color={s.confidence >= 0.8 ? 'green' : 'orange'}>
                    置信 {s.confidence.toFixed(2)}
                  </Tag>
                </Tooltip>
              </Space>
            }
            description={
              <>
                <Paragraph style={{ marginBottom: 4 }}>{s.detail}</Paragraph>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  证据:{JSON.stringify(s.evidence)}
                </Text>
              </>
            }
          />
        </List.Item>
      )}
    />
  )

  return (
    <Card
      id="optimization"
      size="small"
      style={{ marginBottom: 16 }}
      title={
        <Space>
          <ExperimentOutlined />
          <span>系统自学习中心</span>
          <Badge count={pending.length} showZero style={{ backgroundColor: '#1677ff' }} />
        </Space>
      }
      extra={
        <Space>
          <Button size="small" type="primary" loading={running}
            icon={<ThunderboltOutlined />} onClick={analyze}>
            分析最新标注
          </Button>
          <Button size="small" onClick={exportPack}>导出给开发</Button>
          <Button size="small" onClick={load}>刷新</Button>
        </Space>
      }
    >
      <Alert
        type="info" showIcon style={{ marginBottom: 10 }}
        message="每次人工标注都会被分析,转成可执行的系统改进"
        description="「可自动生效」的采纳后当场写入规则,下一轮识别即受益;「需开发介入」的采纳只代表确认要做,系统行为不会改变,请导出交开发——两者分开显示,避免误以为点了采纳问题就解决了。"
      />
      {loading ? (
        <div style={{ textAlign: 'center', padding: 30 }}><Spin /></div>
      ) : (
        <Tabs
          size="small"
          items={[
            {
              key: 'auto',
              label: `可自动生效（${autoOnes.length}）`,
              children: renderSuggestions(autoOnes, true),
            },
            {
              key: 'dev',
              label: `需开发介入（${devOnes.length}）`,
              children: renderSuggestions(devOnes, false),
            },
            {
              key: 'rules',
              label: `已生效规则（${rules.length}）`,
              children: rules.length ? (
                <Table<LearnedRule>
                  size="small" pagination={false}
                  rowKey={(r) => `${r.rule_type}-${r.rule_key}`}
                  dataSource={rules}
                  columns={[
                    { title: '类型', dataIndex: 'rule_type', width: 130,
                      render: (v: string) => CATEGORY_LABEL[v] ?? v },
                    { title: '规则', width: 320,
                      render: (_: unknown, r) => (
                        <Text code>{r.rule_key} → {r.rule_value}</Text>) },
                    { title: '命中次数', dataIndex: 'hit_count', width: 100 },
                  ]}
                />
              ) : <Empty description="还没有生效的学习规则" />,
            },
            {
              key: 'log',
              label: '过程日志',
              children: runs.length ? (
                <List
                  dataSource={runs}
                  renderItem={(r) => (
                    <List.Item>
                      <Descriptions size="small" column={1} style={{ width: '100%' }}
                        title={
                          <Space>
                            <Text>{new Date(r.started_at).toLocaleString('zh-CN')}</Text>
                            <Tag>{r.trigger}</Tag>
                            <Tag color="blue">扫描 {r.events_scanned} 条标注</Tag>
                            <Tag color={r.findings ? 'green' : 'default'}>
                              产出 {r.findings} 条建议
                            </Tag>
                            {r.error ? <Tag color="red">出错</Tag> : null}
                          </Space>
                        }>
                        <Descriptions.Item label="逐步经过">
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {r.steps_json.map((s) => JSON.stringify(s)).join('  →  ')}
                          </Text>
                        </Descriptions.Item>
                      </Descriptions>
                    </List.Item>
                  )}
                />
              ) : <Empty description="还没有分析记录，点「分析最新标注」开始" />,
            },
          ]}
        />
      )}
    </Card>
  )
}
