import { useState } from 'react'
import { PageContainer } from '@ant-design/pro-components'
import {
  Card, Form, Input, Select, Button, Space, Row, Col, Tag, Empty,
  Descriptions, Typography, List, message, Alert,
} from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import {
  auditText,
  DISCIPLINE_OPTIONS,
  disciplineLabel,
  riskColor,
  scenarioColor,
  type ReviewAuditRequest,
  type ReviewAuditResult,
} from '@/services/drawingReview'
import QuestionPack from './QuestionPack'
import DocumentOutput from './DocumentOutput'

const { TextArea } = Input
const { Paragraph, Text } = Typography

const DOC_TYPE_OPTIONS = [
  { value: '会审记录', label: '会审记录' },
  { value: '设计交底', label: '设计交底' },
  { value: '设计答复', label: '设计答复' },
  { value: '问题单', label: '问题单' },
  { value: '综合协调纪要', label: '综合协调纪要' },
]

function locationItems(loc: ReviewAuditResult['定位信息']) {
  const entries: { label: string; value: string[] }[] = [
    { label: '图号', value: loc.drawings },
    { label: '层位', value: loc.levels },
    { label: '轴线', value: loc.axes },
    { label: '节点/系统', value: loc.nodes_or_systems },
    { label: '空间', value: loc.spaces },
  ]
  return entries.filter((e) => e.value && e.value.length > 0)
}

interface ResultViewProps {
  data: ReviewAuditResult
  source: { title: string; body: string; discipline?: string }
}

function ResultView({ data, source }: ResultViewProps) {
  const judge = data.专业判断
  const risk = data.风险等级
  const loc = locationItems(data.定位信息)
  const obj = data.对象识别
  const scenario = data.场景识别

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制')
    } catch {
      message.warning('复制失败')
    }
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Space wrap>
        <Tag color="blue" style={{ fontSize: 14 }}>
          {disciplineLabel(judge.code)}
        </Tag>
        <Tag color={riskColor(risk.level)}>风险：{risk.level || '—'}</Tag>
        {scenario?.name && (
          <Tag color={scenarioColor(scenario.name)}>场景：{scenario.name}</Tag>
        )}
        {data.问题归类.map((c) => (
          <Tag key={c} color="purple">{c}</Tag>
        ))}
        <Text type="secondary">判定依据：{judge.basis || '—'}</Text>
      </Space>

      {risk.trigger && <Alert type="warning" showIcon message={`风险触发：${risk.trigger}`} />}

      {(obj || scenario) && (
        <Descriptions bordered size="small" column={1} colon>
          {obj && (
            <Descriptions.Item label="对象识别">
              <Space wrap>
                {obj.level && <Tag color="geekblue">{obj.level}</Tag>}
                <Text strong>{obj.object || '—'}</Text>
                {obj.basis && <Text type="secondary">（{obj.basis}）</Text>}
              </Space>
            </Descriptions.Item>
          )}
          {scenario && (
            <Descriptions.Item label="场景识别">
              <Space wrap>
                <Tag color={scenarioColor(scenario.name)}>{scenario.name || '—'}</Tag>
                {scenario.priority_reason && (
                  <Text type="secondary">{scenario.priority_reason}</Text>
                )}
              </Space>
            </Descriptions.Item>
          )}
        </Descriptions>
      )}

      <Descriptions bordered size="small" column={1} colon>
        {data.核心concern.length > 0 && (
          <Descriptions.Item label="核心 concern">
            {data.核心concern.map((c) => `${c.label}（${c.reason}）`).join('；')}
          </Descriptions.Item>
        )}
        {(data.接口复核.primary || data.接口复核.related.length > 0) && (
          <Descriptions.Item label="接口复核">
            {[data.接口复核.primary, ...data.接口复核.related].filter(Boolean).join(' → ')}
            {data.接口复核.reason ? `（${data.接口复核.reason}）` : ''}
          </Descriptions.Item>
        )}
        {loc.map((e) => (
          <Descriptions.Item key={e.label} label={e.label}>
            {e.value.join('、')}
          </Descriptions.Item>
        ))}
        {data.建议动作.length > 0 && (
          <Descriptions.Item label="建议动作">
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              {data.建议动作.map((a, i) => <li key={i}>{a}</li>)}
            </ol>
          </Descriptions.Item>
        )}
        {data.证据缺口.length > 0 && (
          <Descriptions.Item label="证据缺口">{data.证据缺口.join('；')}</Descriptions.Item>
        )}
      </Descriptions>

      {data.问题包 && <QuestionPack pack={data.问题包} />}

      <Card size="small" title="标准问题（可直接入会审问题单）">
        {data.标准问题.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="证据不足，未生成闭环问题" />
        ) : (
          <List
            dataSource={data.标准问题}
            renderItem={(q) => (
              <List.Item actions={[<a key="c" onClick={() => copy(q)}>复制</a>]}>
                <Paragraph style={{ margin: 0 }}>{q}</Paragraph>
              </List.Item>
            )}
          />
        )}
      </Card>

      {/*
        文书化输出：优先渲染 auditText 已返回的「文书输出」；
        若后端未透传则展示空态，并由「按当前文本生成」按钮调用 genDocument 兜底。
      */}
      <DocumentOutput output={data.文书输出} source={source} />
    </Space>
  )
}

export default function DrawingReviewPage() {
  const [form] = Form.useForm<ReviewAuditRequest>()
  const [result, setResult] = useState<ReviewAuditResult | null>(null)
  const [submitted, setSubmitted] = useState<ReviewAuditRequest | null>(null)
  const [loading, setLoading] = useState(false)

  const onSubmit = async (values: ReviewAuditRequest) => {
    setLoading(true)
    try {
      const data = await auditText(values)
      setResult(data)
      setSubmitted(values)
    } catch (e) {
      message.error('审查失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <PageContainer
      header={{ title: '图纸会审审查', subTitle: '基于 1909 条历史会审经验的结构化审查与闭环问题生成' }}
    >
      <Row gutter={16}>
        <Col xs={24} lg={10}>
          <Card title="录入会审/交底记录" size="small">
            <Form form={form} layout="vertical" onFinish={onSubmit} disabled={loading}>
              <Form.Item label="专业（可留空，系统自动反推）" name="discipline">
                <Select allowClear showSearch optionFilterProp="label" options={DISCIPLINE_OPTIONS} placeholder="选择专业" />
              </Form.Item>
              <Form.Item label="文档类型" name="doc_type">
                <Select allowClear options={DOC_TYPE_OPTIONS} placeholder="选择文档类型" />
              </Form.Item>
              <Form.Item label="标题" name="title" rules={[{ required: true, message: '请填写标题' }]}>
                <Input placeholder="如：B2 层③~⑤轴梁标高问题" />
              </Form.Item>
              <Form.Item label="正文 / 问题描述" name="body" rules={[{ required: true, message: '请填写正文' }]}>
                <TextArea rows={10} placeholder="粘贴会审记录、设计交底或问题单正文…" />
              </Form.Item>
              <Form.Item>
                <Button type="primary" htmlType="submit" loading={loading} icon={<ThunderboltOutlined />} block>
                  执行会审审查
                </Button>
              </Form.Item>
            </Form>
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card title="审查结果" size="small" style={{ minHeight: 400 }}>
            {result && submitted ? (
              <ResultView
                data={result}
                source={{
                  title: submitted.title,
                  body: submitted.body,
                  discipline: submitted.discipline,
                }}
              />
            ) : (
              <Empty description="填写左侧表单并执行审查后，结构化结果将显示在此" style={{ marginTop: 80 }} />
            )}
          </Card>
        </Col>
      </Row>
    </PageContainer>
  )
}
