/**
 * 轴网识别面板 —— Phase I 识别链路的**唯一人工出口**。
 *
 * 识别本身是自动的(圈 → 带 → 分区 → 轴号 → 坐标锚点),但有三样东西
 * **必须人看一眼**,此前它们只存在于一次性脚本的 stdout 里,等于没有交付:
 *
 *   1. **分区编号** —— GB/T 50001 §8.0.5 未规定哪个分区是 1,几何推不出。
 *      每个分区确认**一次**(不是每条轴线),确认后后端自动重跑。
 *      **未确认的分区不产出世界锚点**:锚点身份是轴号对,没有分区号就会串图。
 *   2. **粗错坐标** —— RANSAC 判出的 OCR 误读(实测把 -156.750 读成 -1.000)。
 *      错的世界坐标比缺一个锚点危险得多,所以它们不入锚点表,只列在这里。
 *   3. **国标校验违规** —— §8.0.3~8.0.6 自查结果。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Descriptions, Input, Space, Spin, Table, Tag,
  Typography, message,
} from 'antd'
import {
  AnchorSuggestion, AxisRecognitionOutlier, AxisRecognitionResult,
  AxisRecognitionSummaryRow, AxisRecognitionZone, ZonePropagationStats,
  confirmAxisZoneLabel, getAnchorSuggestions, getDrawingAxisRecognition,
  listAxisRecognition, propagateAxisZones, startDrawingAxisRecognition,
  startProjectAxisRecognition,
} from '@/services/projectInfo'

const { Text } = Typography

interface AxisRecognitionPanelProps {
  projectId: string
}

/** 残差警戒线(米)。超过它说明锚点配错或轴号重名,模型宁可不摆也不摆错 */
const RESIDUAL_WARN_M = 0.5

export default function AxisRecognitionPanel({ projectId }: AxisRecognitionPanelProps) {
  const [rows, setRows] = useState<AxisRecognitionSummaryRow[]>([])
  const [pending, setPending] = useState({ outliers: 0, violations: 0, drawings: 0, with_anchors: 0 })
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<AxisRecognitionResult | null>(null)
  const [zoneInput, setZoneInput] = useState<Record<number, string>>({})
  const [propagating, setPropagating] = useState(false)
  const [propagation, setPropagation] = useState<ZonePropagationStats | null>(null)
  const [suggestions, setSuggestions] = useState<AnchorSuggestion[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listAxisRecognition(projectId)
      setRows(data.items || [])
      setPending(data.pending)
    } catch {
      message.error('轴网识别结果加载失败')
    } finally {
      setLoading(false)
    }
    // 荐锚是**辅助信息**，拉不到不该拖垮整个面板 —— 单独 try
    try {
      const res = await getAnchorSuggestions(projectId, 5)
      setSuggestions(res.data?.items || [])
    } catch {
      setSuggestions([])
    }
  }, [projectId])

  /**
   * 把人工确认的分区号传播到其他图。
   *
   * §8.0.5 的分区编号几何推不出，逐张确认不现实。实测「对不上任何锚」占 91%、
   * 歧义仅 1% ⇒ 瓶颈是锚覆盖，所以**确认少数覆盖广的锚图**最划算。
   */
  const runPropagation = async () => {
    setPropagating(true)
    try {
      const res = await propagateAxisZones(projectId)
      setPropagation(res.data)
      message.success(
        res.data.note
          ? res.data.note
          : `传播 ${res.data.propagated} 条，覆盖 ${res.data.drawings_covered ?? 0} 张图`,
      )
      await load()
    } catch (err) {
      message.error('分区号传播失败')
      throw err
    } finally {
      setPropagating(false)
    }
  }

  useEffect(() => { load() }, [load])

  const openDetail = async (drawingId: string) => {
    try {
      setDetail(await getDrawingAxisRecognition(drawingId))
    } catch {
      message.warning('该图尚未识别')
    }
  }

  const confirmZone = async (zoneIndex: number) => {
    if (!detail) return
    const label = (zoneInput[zoneIndex] || '').trim()
    if (!label) { message.warning('请填写分区号,如 1 / 2'); return }
    await confirmAxisZoneLabel(detail.drawing_id, zoneIndex, label)
    message.success(`分区 ${zoneIndex} 已确认为 ${label},正在重跑识别`)
    await load()
    await openDetail(detail.drawing_id)
  }

  const columns = [
    { title: '图号', dataIndex: 'drawing_no', width: 130 },
    { title: '图名', dataIndex: 'title', ellipsis: true },
    { title: '分区', dataIndex: 'zone_count', width: 60 },
    { title: '轴线', dataIndex: 'axis_count', width: 60 },
    {
      title: '附加轴线', dataIndex: 'additional_count', width: 80,
      render: (n: number) => n ? <Tag>{n}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '世界锚点', dataIndex: 'anchor_count', width: 90,
      render: (n: number) => n
        ? <Tag color="green">{n}</Tag>
        : <Tag color="default">待确认分区号</Tag>,
    },
    {
      title: '待核对', key: 'pending', width: 120,
      render: (_: unknown, r: AxisRecognitionSummaryRow) => (
        <Space size={4}>
          {r.outlier_count > 0 && <Tag color="red">粗错 {r.outlier_count}</Tag>}
          {r.violation_count > 0 && <Tag color="orange">违规 {r.violation_count}</Tag>}
          {!r.outlier_count && !r.violation_count && <Text type="secondary">—</Text>}
        </Space>
      ),
    },
    {
      title: '残差', key: 'rmse', width: 90,
      render: (_: unknown, r: AxisRecognitionSummaryRow) => {
        const rmse = r.transform?.rmse_m
        if (rmse == null) return <Text type="secondary">—</Text>
        return (
          <Text type={rmse > RESIDUAL_WARN_M ? 'danger' : 'success'}>
            {(rmse * 1000).toFixed(1)} 毫米
          </Text>
        )
      },
    },
    {
      title: '操作', key: 'op', width: 150,
      render: (_: unknown, r: AxisRecognitionSummaryRow) => (
        <Space size={4}>
          <Button size="small" onClick={() => openDetail(r.drawing_id)}>详情</Button>
          <Button size="small" onClick={async () => {
            await startDrawingAxisRecognition(r.drawing_id)
            message.success('已重新识别,稍后刷新')
          }}>重识别</Button>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="轴网识别"
      extra={
        <Space>
          <Button onClick={load}>刷新</Button>
          {/*
            分区号传播:确认少数覆盖广的锚图，其余按轴距序列自动继承。
            实测未匹配原因中「对不上任何锚」占 91%、歧义仅 1% ⇒
            瓶颈是锚覆盖不足，多确认一张覆盖广的图就扩一片匹配面。
          */}
          <Button loading={propagating} onClick={runPropagation}>
            传播分区号
          </Button>
          <Button type="primary" onClick={async () => {
            await startProjectAxisRecognition(projectId)
            message.success('全项目识别已启动,逐图独立执行')
          }}>全项目识别</Button>
        </Space>
      }
    >
      <Alert
        type="info" showIcon style={{ marginBottom: 12 }}
        message={`已识别 ${pending.drawings} 张,其中 ${pending.with_anchors} 张已产出世界锚点`}
        description={
          <>
            分区编号国标未规定顺序、几何推不出,需<b>每个分区确认一次</b>;
            <b>未确认的分区不产出锚点</b>——锚点身份是轴号对,缺分区号会串图。
            {(pending.outliers > 0 || pending.violations > 0) &&
              ` 另有粗错 ${pending.outliers} 处、国标违规 ${pending.violations} 条待核对。`}
            <br />
            确认后可点「<b>传播分区号</b>」，把已确认的分区经轴距序列匹配扩散到其他图
            ——实测确认 <b>1 张</b>覆盖广的轴网定位图即可覆盖 <b>143 张</b>。
          </>
        }
      />
      {propagation && (
        <Alert
          type={propagation.propagated > 0 ? 'success' : 'warning'}
          showIcon style={{ marginBottom: 12 }}
          message={
            propagation.note
              ? propagation.note
              : `分区号传播:${propagation.propagated} 条,覆盖 ${propagation.drawings_covered ?? 0} 张图`
          }
          description={
            propagation.note ? null : (
              <>
                锚 <b>{propagation.anchor_zones}</b> 组序列（来自 {propagation.anchor_drawings ?? 0} 张
                <b>人工确认</b>的图），候选 {propagation.candidates ?? 0} 组。
                <br />
                传播结果标为<b>自动推导</b>，与人工确认分开存放，
                <b>不会覆盖人工确认</b>；每多确认一张覆盖广的图再跑一次，匹配面就扩一片。
              </>
            )
          }
        />
      )}

      {suggestions.length > 0 && (
        <Card
          size="small" style={{ marginBottom: 12 }}
          title="该确认哪几张最划算"
          extra={
            <Text type="secondary" style={{ fontSize: 12 }}>
              按**实测能多带动几张**排序，不是按轴线多少
            </Text>
          }
        >
          <Table
            rowKey="drawing_id" size="small" pagination={false}
            dataSource={suggestions}
            columns={[
              { title: '图号', dataIndex: 'drawing_no', width: 130 },
              { title: '图名', dataIndex: 'title', ellipsis: true },
              {
                // **排序的真依据**：覆盖力只是代理指标，会被符号场误检刷榜
                // ——实测旧榜首「最长 79 段」的图一张也带不动。
                title: '确认后多带动', dataIndex: 'estimated_drawings', width: 130,
                render: (n?: number) => {
                  if (n === undefined) return <Text type="secondary">未试算</Text>
                  return n > 0
                    ? <Tag color="green"><b>{n}</b> 张</Tag>
                    : <Tag>0 张·确认它不扩大覆盖</Tag>
                },
              },
              {
                // **最长的一组**才是覆盖力：匹配按组做，各组总和会把
                // 「11 个分区各 4 段」这种符号场误检抬成榜首（实测发生过）
                title: '最长序列', dataIndex: 'max_gaps', width: 90,
                render: (n: number) => <Tag color="blue">{n} 段</Tag>,
              },
              {
                title: '方向', dataIndex: 'directions', width: 80,
                // 双向才能构成交点 —— 单向图确认了也拿不到世界坐标
                render: (n: number) =>
                  n >= 2
                    ? <Tag color="green">双向</Tag>
                    : <Tag color="orange">单向</Tag>,
              },
              {
                title: '推荐理由', dataIndex: 'reason', ellipsis: true,
                render: (text: string) => <Text type="secondary">{text}</Text>,
              },
              {
                title: '', width: 80,
                render: (_: unknown, row: AnchorSuggestion) => (
                  <Button type="link" size="small"
                          onClick={() => openDetail(row.drawing_id)}>
                    去确认
                  </Button>
                ),
              },
            ] as never}
          />
        </Card>
      )}

      <Spin spinning={loading}>
        <Table
          rowKey="drawing_id" size="small" columns={columns as never} dataSource={rows}
          pagination={{ pageSize: 10 }}
        />
      </Spin>

      {detail && (
        <Card
          size="small" style={{ marginTop: 16 }}
          title={`识别详情 · ${detail.drawing_id.slice(0, 8)}`}
          extra={<Button size="small" onClick={() => setDetail(null)}>收起</Button>}
        >
          <Descriptions size="small" column={4} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="轴号圈">{detail.circle_count}</Descriptions.Item>
            <Descriptions.Item label="主轴线">{detail.axis_count}</Descriptions.Item>
            <Descriptions.Item label="附加轴线">{detail.additional_count}</Descriptions.Item>
            <Descriptions.Item label="残差">
              {detail.transform ? `${(detail.transform.rmse_m * 1000).toFixed(1)} 毫米` : '—'}
            </Descriptions.Item>
          </Descriptions>

          {detail.is_split_view && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="这是一图多视图（分幅），不是 §8.0.5 的分区"
              description={
                <>
                  <div>
                    各「分区」只标注了单向轴号 —— §8.0.5 的分区在平面上两个方向
                    都标轴号，而立面/剖面是投影图只有一个方向。**分幅没有分区号，
                    因此不需要确认**。
                  </div>
                  {!!detail.split_view_numbering?.length && (
                    <div style={{ marginTop: 8 }}>
                      跨幅连续编号<b>建议</b>（未改写轴号，需人工确认后采用）：
                      {detail.split_view_numbering.map((n) => (
                        <Tag key={n.index} style={{ marginLeft: 4 }}>
                          {`第${n.position + 1}幅 ${n.start}~${n.end}`}
                          {n.overlap_assumed > 0 ? `（搭接${n.overlap_assumed}根）` : ''}
                        </Tag>
                      ))}
                      <div style={{ marginTop: 4, color: '#8c8c8c' }}>
                        注意：「同一视图分幅」应串号，「一页多个独立视图」不该串。
                        两者在轴网几何上形态相同，需看各幅图名区分。
                      </div>
                    </div>
                  )}
                </>
              }
            />
          )}

          <Text strong>分区编号确认</Text>
          <Table
            rowKey="index" size="small" pagination={false}
            style={{ marginTop: 8, marginBottom: 16 }}
            dataSource={detail.zones || []}
            columns={[
              { title: '分区', dataIndex: 'index', width: 60 },
              { title: '数字轴线', dataIndex: 'numeric_axes', width: 90 },
              { title: '字母轴线', dataIndex: 'alpha_axes', width: 90 },
              {
                title: '分区号', key: 'label', width: 220,
                render: (_: unknown, z: AxisRecognitionZone) => {
                  if (z.zone_label) return <Tag color="green">{z.zone_label}</Tag>
                  // 单分区图没有分区号可确认(§8.0.5 只在多分区时才用),
                  // 不要向用户要一个他给不出、也不该给的输入。
                  if (!z.needs_confirmation) {
                    return <Tag>单分区 · 无需分区号</Tag>
                  }
                  return (
                    <Space size={4}>
                      <Input
                        size="small" style={{ width: 90 }} placeholder="如 1"
                        value={zoneInput[z.index] || ''}
                        onChange={(e) => setZoneInput({ ...zoneInput, [z.index]: e.target.value })}
                      />
                      <Button size="small" type="primary"
                        onClick={() => confirmZone(z.index)}>确认</Button>
                    </Space>
                  )
                },
              },
            ] as never}
          />

          {!!detail.outliers?.length && (
            <>
              <Text strong type="danger">粗错坐标(未写入锚点,请人工核对)</Text>
              <Table
                rowKey={(r) => `${r.page[0]}-${r.page[1]}`} size="small" pagination={false}
                style={{ marginTop: 8, marginBottom: 16 }}
                dataSource={detail.outliers}
                columns={[
                  { title: '页面位置', key: 'page', render: (_: unknown, r: AxisRecognitionOutlier) => `(${r.page[0].toFixed(1)}, ${r.page[1].toFixed(1)}) pt` },
                  { title: 'OCR 读到', key: 'world', render: (_: unknown, r: AxisRecognitionOutlier) => `X=${r.world[0]} Y=${r.world[1]}` },
                ] as never}
              />
            </>
          )}

          {!!detail.violations?.length && (
            <>
              <Text strong type="warning">国标校验违规</Text>
              <Table
                rowKey={(_, i) => String(i)} size="small" pagination={false}
                style={{ marginTop: 8 }}
                dataSource={detail.violations}
                columns={[
                  { title: '条款', dataIndex: 'rule', width: 90 },
                  { title: '分区', dataIndex: 'zone_index', width: 60 },
                  { title: '说明', dataIndex: 'detail' },
                ] as never}
              />
            </>
          )}
        </Card>
      )}
    </Card>
  )
}
