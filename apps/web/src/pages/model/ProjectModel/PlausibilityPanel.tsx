/**
 * 现实合理性面板：模型里这些构件，在现实世界里可能存在吗。
 *
 * 与「构件核对」「模型质量」的分工：那两个看的是**与图纸比对得对不对**
 * （要人判读、有滞后）；这一个是**纯计算**的物理体检 —— 自交轮廓、悬浮构件、
 * 层高为负、方量超出量级，建完模当场就能算出来，不等人。
 *
 * 三条界面纪律，都对应后端的同一条纪律：
 * ① 每条结论都显示**依据**与**数字**，否则无法复核；
 * ② 「跑不了的规则」必须显示 —— 少跑一条却让人以为全过，是最坏的结果；
 * ③ 样例被截断时要说明总数，不能让人以为只有这么多。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Empty, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd'
import type {
  PlausibilityReport,
  PlausibilityRuleResult,
  PlausibilitySeverity,
} from '@/services/modelPlausibility'
import { SEVERITY_META, getPlausibility, runPlausibility } from '@/services/modelPlausibility'

const { Text, Paragraph } = Typography

const SEVERITY_ORDER: PlausibilitySeverity[] = ['impossible', 'implausible', 'suspect']

interface PlausibilityPanelProps {
  projectId: string
}

/** 证据是数字对象，按 `键 值` 平铺 —— 不做单位换算，后端写的是什么就显示什么。 */
function formatEvidence(evidence: Record<string, unknown>): string {
  return Object.entries(evidence)
    .map(([key, value]) => `${key}=${typeof value === 'number' ? Number(value.toFixed(4)) : String(value)}`)
    .join('  ')
}

export default function PlausibilityPanel({ projectId }: PlausibilityPanelProps) {
  const [report, setReport] = useState<PlausibilityReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getPlausibility(projectId)
      setReport(res?.data ?? null)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  const onRun = useCallback(async () => {
    setRunning(true)
    try {
      const res = await runPlausibility(projectId)
      setReport(res?.data ?? null)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '分析失败')
    } finally {
      setRunning(false)
    }
  }, [projectId])

  const skipped = useMemo(() => Object.entries(report?.skipped ?? {}), [report])
  const errored = useMemo(() => Object.entries(report?.errored ?? {}), [report])
  const rules = useMemo(() => {
    const order = new Map(SEVERITY_ORDER.map((s, i) => [s, i]))
    return [...(report?.rules ?? [])].sort(
      (a, b) => (order.get(a.severity) ?? 9) - (order.get(b.severity) ?? 9) || b.count - a.count,
    )
  }, [report])

  const columns = [
    {
      title: '结论',
      dataIndex: 'rule',
      render: (_: string, row: PlausibilityRuleResult) => (
        <Space direction="vertical" size={0}>
          <Space size={4}>
            <Tag color={SEVERITY_META[row.severity].color}>{SEVERITY_META[row.severity].label}</Tag>
            <Text code>{row.rule}</Text>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            依据：{row.basis}
          </Text>
        </Space>
      ),
    },
    {
      title: '命中',
      dataIndex: 'count',
      width: 120,
      render: (count: number, row: PlausibilityRuleResult) => {
        const denominator = report?.checked?.[row.rule]
        const truncated = report?.truncated?.[row.rule]
        return (
          <Space direction="vertical" size={0}>
            <Text strong>{denominator ? `${count} / ${denominator}` : count}</Text>
            {truncated ? (
              <Tooltip title={`共 ${truncated} 条，界面只展示前 ${report?.per_rule_cap} 条样例`}>
                <Text type="secondary" style={{ fontSize: 12 }}>样例已截断</Text>
              </Tooltip>
            ) : null}
          </Space>
        )
      },
    },
  ]

  return (
    <Spin spinning={loading}>
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Space wrap>
          {SEVERITY_ORDER.map((severity) => (
            <Tooltip key={severity} title={SEVERITY_META[severity].hint}>
              <Tag color={SEVERITY_META[severity].color}>
                {SEVERITY_META[severity].label} {report?.counts?.[severity] ?? 0}
              </Tag>
            </Tooltip>
          ))}
          <Button size="small" loading={running} onClick={onRun}>
            重新分析
          </Button>
        </Space>

        {error ? <Alert type="error" showIcon message={error} /> : null}

        {!report && !loading ? (
          <Empty description="还没跑过合理性分析（建模完成后会自动跑一次）" />
        ) : null}

        {/* 跑不了 ≠ 通过。这一条必须显示在结论前面，否则「全过」是假的 */}
        {skipped.length ? (
          <Alert
            type="warning"
            showIcon
            message={`有 ${skipped.length} 条规则跑不了（缺数据），它们既不算通过也不算不通过`}
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {skipped.map(([rule, reason]) => (
                  <li key={rule}>
                    <Text code>{rule}</Text>：{reason}
                  </li>
                ))}
              </ul>
            }
          />
        ) : null}

        {errored.length ? (
          <Alert
            type="error"
            showIcon
            message={`有 ${errored.length} 条规则执行出错（是程序缺陷，不是模型问题）`}
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {errored.map(([rule, reason]) => (
                  <li key={rule}>
                    <Text code>{rule}</Text>：{reason}
                  </li>
                ))}
              </ul>
            }
          />
        ) : null}

        {rules.length ? (
          <Table<PlausibilityRuleResult>
            rowKey="rule"
            size="small"
            pagination={false}
            dataSource={rules}
            columns={columns}
            expandable={{
              expandedRowRender: (row) => (
                <Space direction="vertical" size={2} style={{ width: '100%' }}>
                  {row.samples.map((sample) => (
                    <Paragraph key={sample.target} style={{ margin: 0, fontSize: 12 }}>
                      <Text code>{sample.target}</Text> {sample.detail}
                      {Object.keys(sample.evidence || {}).length ? (
                        <Text type="secondary">　{formatEvidence(sample.evidence)}</Text>
                      ) : null}
                    </Paragraph>
                  ))}
                </Space>
              ),
            }}
          />
        ) : null}

        {report?.analyzed_at ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            分析于 {String(report.analyzed_at)}（模型版本 {report.model_version ?? '—'}）
          </Text>
        ) : null}
      </Space>
    </Spin>
  )
}
