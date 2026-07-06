import { useEffect, useMemo, useState } from 'react'
import {
  Card, Tag, Space, Button, Empty, Typography, Descriptions, Divider, message,
} from 'antd'
import { CopyOutlined, DownloadOutlined } from '@ant-design/icons'
import { getAiReviewIssues } from '@/services/drawings'
import {
  closureColor,
  dimensionColor,
  disciplineLabel,
  riskColor,
  scenarioColor,
  type QuestionPack,
  type ReviewMethod,
  type ReviewSop,
} from '@/services/reviewAudit'

const { Text, Paragraph } = Typography

/** ai_review_issues 中 engine==='review' 的一行（会审审查引擎扩展字段） */
interface ReviewIssue {
  id: string
  engine: string
  severity: string
  description: string
  discipline_code?: string
  risk_level?: string
  object_level?: string
  standard_question?: string
  interface_primary?: string
  location_json?: unknown
  concerns?: unknown
  issue_class?: unknown
  interface_related?: unknown
  evidence_gap?: unknown
  // 契约 V2 透传字段
  object_name?: string
  scenario?: string
  question_pack?: unknown
  // 契约 V3 透传字段（SOP 逐项清单核查）
  review_sop?: unknown
  // 契约 V4 透传字段（方法论：控制链/五维审查/处理建议）
  review_method?: unknown
}

interface Props {
  drawingId: string
  reportStatus: string
}

const FETCH_LIMIT = 200

/** JSONB 字段经 asyncpg 可能以 JSON 文本返回，统一安全解析为数组/对象。 */
function coerce<T>(value: unknown, fallback: T): T {
  if (value == null) return fallback
  if (typeof value === 'string') {
    try {
      return JSON.parse(value) as T
    } catch {
      return fallback
    }
  }
  return value as T
}

function locationLine(loc: Record<string, string[]>): string {
  const parts: string[] = []
  const push = (label: string, arr?: string[]) => {
    if (arr && arr.length) parts.push(`${label}：${arr.join('、')}`)
  }
  push('图号', loc.drawings)
  push('层位', loc.levels)
  push('轴线', loc.axes)
  push('节点/系统', loc.nodes_or_systems)
  push('空间', loc.spaces)
  return parts.join('  ')
}

function buildIssueSheet(issues: ReviewIssue[]): string {
  const header = '序号\t专业\t风险\t问题归类\t标准问题\t接口\t证据缺口\t处理建议\t闭环判定'
  const rows = issues.map((it, i) => {
    const cls = coerce<string[]>(it.issue_class, []).join('/')
    const related = coerce<string[]>(it.interface_related, [])
    const iface = [it.interface_primary, ...related].filter(Boolean).join('、')
    const gap = coerce<string[]>(it.evidence_gap, []).join('；')
    const q = it.standard_question || it.description
    const method = coerce<Partial<ReviewMethod>>(it.review_method, {})
    const actions = (method.处理建议 ?? [])
      .map((a) => `${a.动作类型}：${a.动作}（责任方：${a.责任方}，输出件：${a.输出件}）`)
      .join('；')
    const closure = method.控制链?.闭环判定?.status ?? ''
    return `${i + 1}\t${disciplineLabel(it.discipline_code)}\t${it.risk_level ?? ''}\t${cls}\t${q}\t${iface}\t${gap}\t${actions}\t${closure}`
  })
  return [header, ...rows].join('\n')
}

export default function ReviewFindings({ drawingId, reportStatus }: Props) {
  const [issues, setIssues] = useState<ReviewIssue[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (reportStatus !== 'done') return
    let cancelled = false
    setLoading(true)
    getAiReviewIssues(drawingId, { limit: FETCH_LIMIT, offset: 0 })
      .then((res: { items?: ReviewIssue[] }) => {
        if (cancelled) return
        const reviewOnly = (res.items ?? []).filter((i) => i.engine === 'review')
        setIssues(reviewOnly)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [drawingId, reportStatus])

  const grouped = useMemo(() => {
    const map: Record<string, ReviewIssue[]> = {}
    for (const it of issues) {
      const key = it.discipline_code || '未分类'
      ;(map[key] ??= []).push(it)
    }
    return map
  }, [issues])

  const handleExport = () => {
    if (!issues.length) return
    const blob = new Blob([buildIssueSheet(issues)], { type: 'text/tab-separated-values;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `会审问题单_${drawingId.slice(0, 8)}.tsv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制问题句')
    } catch {
      message.warning('复制失败，请手动选择文本')
    }
  }

  if (reportStatus !== 'done') return null
  if (!loading && !issues.length) {
    return <Empty description="本次审查未产生会审问题" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Text type="secondary">共 {issues.length} 条会审问题，按专业分组</Text>
        <Button size="small" icon={<DownloadOutlined />} onClick={handleExport} disabled={!issues.length}>
          导出会审问题单
        </Button>
      </Space>

      {Object.entries(grouped).map(([code, list]) => (
        <Card
          key={code}
          size="small"
          loading={loading}
          title={<Space>{disciplineLabel(code)}<Tag>{list.length} 条</Tag></Space>}
          style={{ marginBottom: 12 }}
        >
          {list.map((it, idx) => {
            const loc = coerce<Record<string, string[]>>(it.location_json, {})
            const concerns = coerce<{ label: string; reason: string }[]>(it.concerns, [])
            const cls = coerce<string[]>(it.issue_class, [])
            const related = coerce<string[]>(it.interface_related, [])
            const gap = coerce<string[]>(it.evidence_gap, [])
            const pack = coerce<Partial<QuestionPack>>(it.question_pack, {})
            const sop = coerce<Partial<ReviewSop>>(it.review_sop, {})
            const cov = sop.checklist
            const upgradeGaps = (cov?.uncovered ?? []).filter((u) => u.升级)
            const locText = locationLine(loc)
            const method = coerce<Partial<ReviewMethod>>(it.review_method, {})
            const chain = method.控制链
            const verdict = chain?.闭环判定
            const suspectDims = (method.五维审查 ?? []).filter((d) => d.状态 === '存疑')
            const structuredActions = method.处理建议 ?? []
            const closureReq = method.闭环要求
            const priorityHits = method.优先对象 ?? []
            return (
              <div key={it.id}>
                {idx > 0 && <Divider style={{ margin: '12px 0' }} />}
                <Space wrap style={{ marginBottom: 6 }}>
                  <Tag color={riskColor(it.risk_level)}>风险：{it.risk_level || '—'}</Tag>
                  {it.scenario && (
                    <Tag color={scenarioColor(it.scenario)}>场景：{it.scenario}</Tag>
                  )}
                  {it.object_level && <Tag color="geekblue">{it.object_level}</Tag>}
                  {it.object_name && <Tag color="blue">{it.object_name}</Tag>}
                  {cls.map((c) => (
                    <Tag key={c} color="purple">{c}</Tag>
                  ))}
                  {verdict?.status && (
                    <Tag color={closureColor(verdict.status)}>{verdict.status}</Tag>
                  )}
                  {priorityHits.map((o) => (
                    <Tag key={o.name} color="magenta">优先对象：{o.name}</Tag>
                  ))}
                </Space>
                <Paragraph
                  strong
                  copyable={{ icon: <CopyOutlined />, tooltips: ['复制', '已复制'] }}
                  style={{ marginBottom: 8 }}
                  onClick={() => it.standard_question && handleCopy(it.standard_question)}
                >
                  {it.standard_question || it.description}
                </Paragraph>
                <Descriptions size="small" column={1} colon>
                  {sop.protected_result && (
                    <Descriptions.Item label="受保护结果">{sop.protected_result}</Descriptions.Item>
                  )}
                  {sop.future_impact?.stage && (
                    <Descriptions.Item label="未来影响">
                      <Tag color="gold">{sop.future_impact.stage}</Tag>
                      {sop.future_impact.effect}
                    </Descriptions.Item>
                  )}
                  {cov && cov.checked > 0 && (
                    <Descriptions.Item label="SOP 清单覆盖">
                      <Space wrap>
                        <Tag color={cov.ratio >= 0.8 ? 'green' : cov.ratio >= 0.5 ? 'orange' : 'red'}>
                          {Math.round(cov.ratio * 100)}%（{cov.covered}/{cov.checked}）
                        </Tag>
                        {upgradeGaps.map((u) => (
                          <Tag key={u.检查项} color="volcano">待核：{u.检查项}</Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  )}
                  {pack.主问题 && (
                    <Descriptions.Item label="主问题">{pack.主问题}</Descriptions.Item>
                  )}
                  {pack.补充问题 && (
                    <Descriptions.Item label="补充问题">{pack.补充问题}</Descriptions.Item>
                  )}
                  {concerns.length > 0 && (
                    <Descriptions.Item label="核心 concern">
                      {concerns.map((c) => `${c.label}（${c.reason}）`).join('；')}
                    </Descriptions.Item>
                  )}
                  {(it.interface_primary || related.length > 0) && (
                    <Descriptions.Item label="接口复核">
                      {[it.interface_primary, ...related].filter(Boolean).join(' → ')}
                    </Descriptions.Item>
                  )}
                  {locText && <Descriptions.Item label="定位信息">{locText}</Descriptions.Item>}
                  {gap.length > 0 && (
                    <Descriptions.Item label="证据缺口">{gap.join('；')}</Descriptions.Item>
                  )}
                  {(method.五维审查 ?? []).length > 0 && (
                    <Descriptions.Item label="五维审查">
                      <Space wrap>
                        {(method.五维审查 ?? []).map((d) => (
                          <Tag key={d.维度} color={dimensionColor(d.状态)}>
                            {d.维度}：{d.状态}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  )}
                  {suspectDims.length > 0 && (
                    <Descriptions.Item label="存疑依据">
                      {suspectDims.map((d) => `${d.维度}（${d.依据}）`).join('；')}
                    </Descriptions.Item>
                  )}
                  {chain && (chain.触发 || chain.边界 || chain.责任) && (
                    <Descriptions.Item label="控制链">
                      {[
                        chain.触发 && `触发：${chain.触发}`,
                        chain.边界 && `边界：${chain.边界}`,
                        chain.风险 && `风险：${chain.风险}`,
                        chain.责任 && `责任：${chain.责任}`,
                        chain.闭环 && `闭环：${chain.闭环}`,
                      ]
                        .filter(Boolean)
                        .join(' → ')}
                    </Descriptions.Item>
                  )}
                  {structuredActions.length > 0 && (
                    <Descriptions.Item label="处理建议">
                      <div>
                        {structuredActions.map((a) => (
                          <div key={`${a.动作类型}-${a.动作}`}>
                            <Tag color="cyan">{a.动作类型}</Tag>
                            {a.动作}（责任方：{a.责任方}
                            {a.配合方.length > 0 && `，配合：${a.配合方.join('、')}`}
                            ，输出件：{a.输出件}）
                          </div>
                        ))}
                      </div>
                    </Descriptions.Item>
                  )}
                  {verdict && verdict.追问项.length > 0 && (
                    <Descriptions.Item label="闭环追问">
                      {verdict.追问项.join('；')}
                    </Descriptions.Item>
                  )}
                  {closureReq && (
                    <Descriptions.Item label="闭环要求">
                      <Space wrap>
                        <Tag color={closureReq.是否影响开工 ? 'red' : 'default'}>
                          影响开工：{closureReq.是否影响开工 ? '是' : '否'}
                        </Tag>
                        <Tag color={closureReq.是否影响穿插 ? 'orange' : 'default'}>
                          影响穿插：{closureReq.是否影响穿插 ? '是' : '否'}
                        </Tag>
                        <Tag color={closureReq.是否需要专题会 ? 'gold' : 'default'}>
                          需专题会：{closureReq.是否需要专题会 ? '是' : '否'}
                        </Tag>
                      </Space>
                      {closureReq.下次复核节点 && (
                        <div>
                          <Text type="secondary">下次复核：{closureReq.下次复核节点}</Text>
                        </div>
                      )}
                    </Descriptions.Item>
                  )}
                </Descriptions>
              </div>
            )
          })}
        </Card>
      ))}
    </div>
  )
}
