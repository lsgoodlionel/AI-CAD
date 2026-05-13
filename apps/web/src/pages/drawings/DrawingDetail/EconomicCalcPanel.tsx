/**
 * 经济测算面板 — 钢筋翻样 + 下料优化
 * GB50010-2010 锚固/搭接公式 + FFD 下料优化
 */
import { useState } from 'react'
import {
  Card, Form, Select, InputNumber, Button, Table, Space,
  Statistic, Row, Col, Tag, Alert, Divider, message, Spin,
} from 'antd'
import { CalculatorOutlined, PlusOutlined, DeleteOutlined, BulbOutlined } from '@ant-design/icons'
import { runEconomicCalc, getEconomicCalc, type BarItemInput } from '@/services/drawings'

const { Option } = Select

const CONCRETE_GRADES = ['C20', 'C25', 'C30', 'C35', 'C40', 'C45', 'C50']
const STEEL_GRADES = ['HRB335', 'HRB400', 'HRB500', 'HRBF400', 'HRBF500', 'HPB300']
const DIAMETERS = [6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32]

type AnchorRow = {
  diameter: number
  steel_grade: string
  La: number
  LaE: number
  Ll_25: number
  Ll_50: number
  Ll_100: number
}

type CutRow = {
  key: string
  standard_length: number
  cuts: number[]
  waste: number
  repeat: number
  waste_pct: string
}

type CalcResult = {
  anchor_lengths: AnchorRow[]
  cutting_patterns: { standard_length: number; cuts: number[]; waste: number; repeat: number }[]
  total_steel_kg: number
  field_waste_rate: number
  optimized_waste_rate: number
  saving_kg: number
  saving_yuan: number
  auto_proposal_eligible: boolean
  calculated_at?: string
}

interface Props {
  drawingId: string
  drawingNo?: string
  onProposal?: (savingYuan: number) => void
}

export default function EconomicCalcPanel({ drawingId, drawingNo, onProposal }: Props) {
  const [form] = Form.useForm()
  const [bars, setBars] = useState<BarItemInput[]>([
    { diameter: 20, steel_grade: 'HRB400', required_length: 3000, count: 10 },
  ])
  const [result, setResult] = useState<CalcResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [fetching, setFetching] = useState(false)

  const addBar = () =>
    setBars(prev => [...prev, { diameter: 16, steel_grade: 'HRB400', required_length: 2000, count: 5 }])

  const removeBar = (idx: number) =>
    setBars(prev => prev.filter((_, i) => i !== idx))

  const updateBar = (idx: number, field: keyof BarItemInput, val: number | string) =>
    setBars(prev => prev.map((b, i) => i === idx ? { ...b, [field]: val } : b))

  const handleCalc = async () => {
    try {
      const vals = await form.validateFields()
      setLoading(true)
      const res = await runEconomicCalc(drawingId, { ...vals, bars })
      setResult(res)
      message.success('计算完成')
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'errorFields' in e) return
      message.error('计算失败')
    } finally {
      setLoading(false)
    }
  }

  const handleLoadLast = async () => {
    setFetching(true)
    try {
      const res = await getEconomicCalc(drawingId)
      if (res?.exists === false) { message.info('暂无历史计算结果'); return }
      setResult(res)
    } finally {
      setFetching(false)
    }
  }

  const anchorColumns = [
    { title: '直径(mm)', dataIndex: 'diameter', width: 80 },
    { title: '级别', dataIndex: 'steel_grade', width: 90 },
    { title: 'La(mm)', dataIndex: 'La', width: 80 },
    { title: 'LaE(mm)', dataIndex: 'LaE', width: 85 },
    { title: 'Ll≤25%(mm)', dataIndex: 'Ll_25', width: 100 },
    { title: 'Ll 50%(mm)', dataIndex: 'Ll_50', width: 100 },
    { title: 'Ll 100%(mm)', dataIndex: 'Ll_100', width: 105 },
  ]

  const cutRows: CutRow[] = (result?.cutting_patterns ?? []).map((p, i) => ({
    key: String(i),
    standard_length: p.standard_length,
    cuts: p.cuts,
    waste: p.waste,
    repeat: p.repeat,
    waste_pct: ((p.waste / p.standard_length) * 100).toFixed(1) + '%',
  }))

  const cutColumns = [
    { title: '定尺长度(mm)', dataIndex: 'standard_length', width: 110 },
    {
      title: '切割方案',
      dataIndex: 'cuts',
      render: (cuts: number[]) => (
        <Space size={4} wrap>
          {cuts.map((c, i) => <Tag key={i} style={{ fontFamily: 'monospace' }}>{c}</Tag>)}
        </Space>
      ),
    },
    { title: '余料(mm)', dataIndex: 'waste', width: 90 },
    { title: '余料率', dataIndex: 'waste_pct', width: 80 },
    { title: '重复次数', dataIndex: 'repeat', width: 90 },
  ]

  return (
    <Card
      title={<Space><CalculatorOutlined />经济测算 — 钢筋翻样</Space>}
      style={{ marginTop: 16 }}
      extra={
        <Button size="small" loading={fetching} onClick={handleLoadLast}>
          读取上次结果
        </Button>
      }
    >
      {/* 参数表单 */}
      <Form form={form} layout="inline" initialValues={{ concrete_grade: 'C30', seismic_grade: 2, steel_price_per_ton: 4500 }}>
        <Form.Item name="concrete_grade" label="混凝土强度" rules={[{ required: true }]}>
          <Select style={{ width: 90 }}>
            {CONCRETE_GRADES.map(g => <Option key={g} value={g}>{g}</Option>)}
          </Select>
        </Form.Item>
        <Form.Item name="seismic_grade" label="抗震等级" rules={[{ required: true }]}>
          <Select style={{ width: 70 }}>
            {[1, 2, 3, 4].map(g => <Option key={g} value={g}>{g}级</Option>)}
          </Select>
        </Form.Item>
        <Form.Item name="steel_price_per_ton" label="钢筋单价(元/吨)" rules={[{ required: true }]}>
          <InputNumber min={1000} max={20000} step={100} style={{ width: 130 }} />
        </Form.Item>
      </Form>

      {/* 钢筋录入表 */}
      <Divider orientation="left" plain style={{ marginTop: 16 }}>钢筋明细</Divider>
      <Table
        size="small"
        pagination={false}
        dataSource={bars.map((b, i) => ({ ...b, key: i }))}
        columns={[
          {
            title: '直径(mm)',
            dataIndex: 'diameter',
            width: 100,
            render: (v, _, i) => (
              <Select value={v} style={{ width: 80 }} onChange={val => updateBar(i, 'diameter', val)}>
                {DIAMETERS.map(d => <Option key={d} value={d}>φ{d}</Option>)}
              </Select>
            ),
          },
          {
            title: '钢筋级别',
            dataIndex: 'steel_grade',
            width: 120,
            render: (v, _, i) => (
              <Select value={v} style={{ width: 105 }} onChange={val => updateBar(i, 'steel_grade', val)}>
                {STEEL_GRADES.map(g => <Option key={g} value={g}>{g}</Option>)}
              </Select>
            ),
          },
          {
            title: '单根长度(mm)',
            dataIndex: 'required_length',
            width: 130,
            render: (v, _, i) => (
              <InputNumber min={100} max={24000} value={v} style={{ width: 110 }}
                onChange={val => updateBar(i, 'required_length', val ?? 0)} />
            ),
          },
          {
            title: '根数',
            dataIndex: 'count',
            width: 90,
            render: (v, _, i) => (
              <InputNumber min={1} max={9999} value={v} style={{ width: 75 }}
                onChange={val => updateBar(i, 'count', val ?? 1)} />
            ),
          },
          {
            title: '',
            key: 'del',
            width: 50,
            render: (_, __, i) => (
              <Button size="small" danger icon={<DeleteOutlined />}
                disabled={bars.length <= 1}
                onClick={() => removeBar(i)} />
            ),
          },
        ]}
        footer={() => (
          <Button size="small" icon={<PlusOutlined />} onClick={addBar}>
            添加钢筋
          </Button>
        )}
      />

      <div style={{ marginTop: 12, textAlign: 'right' }}>
        <Button type="primary" icon={<CalculatorOutlined />} loading={loading} onClick={handleCalc}>
          开始测算
        </Button>
      </div>

      {/* 计算结果 */}
      {result && (
        <Spin spinning={loading}>
          <Divider />

          {result.auto_proposal_eligible && (
            <Alert
              type="success"
              showIcon
              icon={<BulbOutlined />}
              style={{ marginBottom: 16 }}
              message={`节约额 ¥${result.saving_yuan.toFixed(0)} 元，已达创效提案推送阈值`}
              action={
                onProposal && (
                  <Button size="small" type="primary" onClick={() => onProposal(result.saving_yuan)}>
                    发起创效提案
                  </Button>
                )
              }
            />
          )}

          {/* 汇总统计 */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={4}>
              <Statistic title="总用钢量" value={result.total_steel_kg.toFixed(1)} suffix="kg" />
            </Col>
            <Col span={4}>
              <Statistic
                title="粗放损耗率"
                value={(result.field_waste_rate * 100).toFixed(1)}
                suffix="%"
                valueStyle={{ color: '#cf1322' }}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="翻样后损耗率"
                value={(result.optimized_waste_rate * 100).toFixed(1)}
                suffix="%"
                valueStyle={{ color: '#3f8600' }}
              />
            </Col>
            <Col span={4}>
              <Statistic title="节约钢材" value={result.saving_kg.toFixed(1)} suffix="kg" valueStyle={{ color: '#3f8600' }} />
            </Col>
            <Col span={4}>
              <Statistic
                title="节约金额"
                value={result.saving_yuan.toFixed(0)}
                prefix="¥"
                valueStyle={{ color: '#3f8600', fontWeight: 700 }}
              />
            </Col>
          </Row>

          {/* 锚固/搭接长度表 */}
          <Divider orientation="left" plain>锚固 & 搭接长度（GB50010-2010）</Divider>
          <Table
            size="small"
            dataSource={result.anchor_lengths.map((r, i) => ({ ...r, key: i }))}
            columns={anchorColumns}
            pagination={false}
          />

          {/* 切割方案表 */}
          <Divider orientation="left" plain>下料切割方案</Divider>
          <Table
            size="small"
            dataSource={cutRows}
            columns={cutColumns}
            pagination={false}
          />

          {result.calculated_at && (
            <div style={{ marginTop: 8, fontSize: 12, color: '#888' }}>
              计算时间：{new Date(result.calculated_at).toLocaleString('zh-CN')}
            </div>
          )}
        </Spin>
      )}
    </Card>
  )
}
