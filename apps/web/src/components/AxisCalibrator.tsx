/**
 * 人工标定轴线基准 —— 在图纸上指定轴线作为系统识别的参考系。
 *
 * 为什么需要:自动轴号识别撞到 OCR 物理上限(档案数据位置序与数值序仅 0.3% 一致;
 * 轴号圈+圈内 OCR 后逆序率 0.60→0.21,仍未过 0.15 门槛)。人指定少量基准即可绕开,
 * 系统在此之上做大范围识别与传播。标定时机不限:上传时 / 建模中 / 建模后修正。
 *
 * 两种标定形式:
 * - **单条**:照着图纸内容直接点中某一条线(吸附到候选轴线)并命名;
 * - **批量**:一次点选多条线,只填起止轴号 + 命名方向,系统自动按序派轴号。
 *
 * 附加收益:填了相邻轴距后可**反算比例尺**——直接量图↔实物比值,比读图上文字更可靠。
 *
 * 四种标定模式:
 * - **单条**:点中一条候选线并命名;
 * - **批量**:连点多条,只填起止轴号 + 命名方向,系统自动派号;
 * - **选点**:点一个点写轴号对(如 1轴-A轴),同时生成竖向 + 横向两条轴线,
 *   可顺带填工程坐标 XYZ——两个带坐标的交叉点即可把整张图摆进模型坐标系;
 * - **平移**:拖动已标定的轴线微调位置(自动识别的、人工画的都能拖)。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Checkbox, Input, InputNumber, Modal, Radio, Segmented, Space,
  Spin, Table, Tag, Typography, message,
} from 'antd'
import {
  deleteIntersection, deleteManualAxis, deriveScaleFromAxes, getDrawingPreview,
  listAutoAxes, listAxisLineCandidates, listIntersections, listManualAxes, moveManualAxis,
  relabelManualAxes, saveIntersection, saveManualAxesBatch, saveManualAxis,
  solveWorldAnchor,
  type AutoAxis, type AxisIntersection, type AxisLineCandidate, type AxisNamingOrder,
  type DrawingPreview, type ManualAxis,
} from '@/services/projectInfo'
import ZoomPanViewer from './ZoomPanViewer'

const { Text } = Typography

/** 点击吸附半径(归一化坐标):超过此距离视为没点中任何候选线 */
const SNAP_RADIUS = 0.012
/** 命名方向选项随轴线方向切换:竖向轴线按左右排,横向轴线按上下排 */
const ORDER_OPTIONS: Record<'x' | 'y', { value: AxisNamingOrder; label: string }[]> = {
  x: [
    { value: 'left_to_right', label: '从左到右' },
    { value: 'right_to_left', label: '从右到左' },
  ],
  y: [
    { value: 'top_to_bottom', label: '从上到下' },
    { value: 'bottom_to_top', label: '从下到上' },
  ],
}

interface AxisCalibratorProps {
  projectId: string
  drawingId: string | null
  title?: string
  onClose: () => void
}

/** 点到候选线的距离(轴对齐线):落在线的延伸范围外则不算命中 */
function distanceToCandidate(c: AxisLineCandidate, px: number, py: number): number {
  if (c.direction === 'x') {
    const [lo, hi] = [Math.min(c.y1_norm, c.y2_norm), Math.max(c.y1_norm, c.y2_norm)]
    if (py < lo - SNAP_RADIUS || py > hi + SNAP_RADIUS) return Infinity
    return Math.abs(px - (c.x1_norm + c.x2_norm) / 2)
  }
  const [lo, hi] = [Math.min(c.x1_norm, c.x2_norm), Math.max(c.x1_norm, c.x2_norm)]
  if (px < lo - SNAP_RADIUS || px > hi + SNAP_RADIUS) return Infinity
  return Math.abs(py - (c.y1_norm + c.y2_norm) / 2)
}

export default function AxisCalibrator({
  projectId, drawingId, title, onClose,
}: AxisCalibratorProps) {
  const [preview, setPreview] = useState<DrawingPreview | null>(null)
  const [axes, setAxes] = useState<ManualAxis[]>([])
  const [candidates, setCandidates] = useState<AxisLineCandidate[]>([])
  const [loading, setLoading] = useState(false)
  const [imgH, setImgH] = useState(0)
  const [viewScale, setViewScale] = useState(1)

  const [mode, setMode] = useState<'single' | 'batch' | 'point' | 'move'>('single')
  // 选点定轴:轴号对 + 可选工程坐标
  const [points, setPoints] = useState<AxisIntersection[]>([])
  const [labelX, setLabelX] = useState('')
  const [labelY, setLabelY] = useState('')
  const [world, setWorld] = useState<{ x?: number; y?: number; z?: number }>({})
  // 平移:先选中一条已标定轴线,再点图上目标位置
  const [movingLabel, setMovingLabel] = useState<string | null>(null)
  const [direction, setDirection] = useState<'x' | 'y'>('x')
  const [label, setLabel] = useState('')
  const [spacing, setSpacing] = useState<number | null>(null)
  const [startLabel, setStartLabel] = useState('')
  const [endLabel, setEndLabel] = useState('')
  const [order, setOrder] = useState<AxisNamingOrder>('left_to_right')
  const [picked, setPicked] = useState<number[]>([])       // 选中的候选线下标
  const [pending, setPending] = useState<{ x: number; y: number } | null>(null)
  const [memoryCount, setMemoryCount] = useState(0)
  // 自动已识别的轴线(带轴号):人得看得见系统认成什么,才知道该补哪条、改哪条
  const [autoAxes, setAutoAxes] = useState<AutoAxis[]>([])
  const [showAuto, setShowAuto] = useState(true)
  // 选中已有交叉点 → 改它的工程坐标(而不是只能新建)
  const [editingPoint, setEditingPoint] = useState<AxisIntersection | null>(null)
  // 已标定轴线的多选:选中若干条后可合并成一组统一重新派号
  const [selectedLabels, setSelectedLabels] = useState<string[]>([])
  const imgRef = useRef<HTMLImageElement>(null)
  // 平移(拖拽)结束时浏览器仍会派发 click,会误选到线上。记按下点,
  // 位移超阈值即判为平移,丢弃这次 click。
  const downAt = useRef<{ x: number; y: number } | null>(null)

  const reload = () => {
    if (!drawingId) return
    listManualAxes(projectId, drawingId)
      .then((r) => setAxes(r.axes))
      .catch(() => message.error('已标定轴线加载失败'))
    listIntersections(projectId, drawingId)
      .then((r) => setPoints(r.intersections))
      .catch(() => setPoints([]))
    listAutoAxes(projectId, drawingId)
      .then((r) => setAutoAxes(r.axes))
      .catch(() => setAutoAxes([]))
  }

  useEffect(() => {
    if (!drawingId) return
    setLoading(true)
    setPreview(null); setAxes([]); setCandidates([]); setPicked([])
    setPending(null); setImgH(0); setSelectedLabels([])
    setPoints([]); setMovingLabel(null); setAutoAxes([]); setEditingPoint(null)
    getDrawingPreview(drawingId, { raster: true })
      .then(setPreview)
      .catch(() => message.error('图纸预览加载失败'))
      .finally(() => setLoading(false))
    listAxisLineCandidates(projectId, drawingId)
      .then((r) => { setCandidates(r.candidates); setMemoryCount(r.from_memory) })
      .catch(() => { setCandidates([]); setMemoryCount(0) })   // 抽不出候选就退回手描
    reload()
  }, [projectId, drawingId])

  // 方向切换时,命名顺序与已选线跟着复位,避免选竖线却按上下排的错配
  useEffect(() => {
    setOrder(ORDER_OPTIONS[direction][0].value)
    setPicked([])
  }, [direction])

  const visibleCandidates = useMemo(
    () => candidates.map((c, i) => ({ c, i })).filter(({ c }) => c.direction === direction),
    [candidates, direction],
  )

  const PAN_THRESHOLD_PX = 4

  /** 点图:优先吸附到候选线;附近没有候选线才退回手描两点 */
  const handleClick = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!imgH) return
    const down = downAt.current
    downAt.current = null
    if (down && (Math.abs(e.clientX - down.x) > PAN_THRESHOLD_PX
      || Math.abs(e.clientY - down.y) > PAN_THRESHOLD_PX)) {
      return                       // 这是一次平移,不是点选
    }
    const rect = e.currentTarget.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.height
    const py = (e.clientY - rect.top) / rect.height

    let best = -1
    let bestDist = SNAP_RADIUS
    visibleCandidates.forEach(({ c, i }) => {
      const d = distanceToCandidate(c, px, py)
      if (d < bestDist) { bestDist = d; best = i }
    })

    // 选点定轴:优先吸附到既有轴线的交点,并把它们的轴号预填上——
    // 「在已有轴线上加交叉点」就是这条路径,人不用再手抄轴号
    if (mode === 'point') {
      // 先看是不是点在已有交叉点上——那是要改它的坐标,不是再建一个重复的
      const existing = pointAt2(px, py)
      if (existing) {
        setEditingPoint(existing)
        setLabelX(existing.label_x); setLabelY(existing.label_y)
        setWorld({ x: existing.world_x ?? undefined,
                   y: existing.world_y ?? undefined,
                   z: existing.world_z ?? undefined })
        message.info(`已选中 ${existing.label_x}轴-${existing.label_y}轴，可改工程坐标后保存`)
        return
      }
      const hit = nearestIntersection(px, py)
      if (hit && (!labelX.trim() || !labelY.trim())) {
        setLabelX(hit.labelX); setLabelY(hit.labelY)
        message.info(`已吸附到 ${hit.labelX}轴-${hit.labelY}轴 交点,轴号已填入,再点一次确认`)
        return
      }
      if (!labelX.trim() || !labelY.trim()) {
        message.warning('请先填写交叉的两个轴号(如 1 和 A),或点在两条已有轴线的交点上')
        return
      }
      submitPoint(hit ? hit.x : px, hit ? hit.y : py)
      return
    }
    // 平移:先在下方表格选中一条轴线,再点图上目标位置
    if (mode === 'move') {
      if (!movingLabel) { message.warning('请先在下方表格选中要平移的轴线'); return }
      submitMove(px, py)
      return
    }

    if (best >= 0) {
      setPending(null)
      if (mode === 'batch') {
        setPicked((prev) =>
          prev.includes(best) ? prev.filter((k) => k !== best) : [...prev, best])
        return
      }
      if (!label.trim()) { message.warning('请先填写轴号'); return }
      saveSingle(candidates[best])
      return
    }

    if (mode === 'batch') { message.warning('未点中候选轴线,请点在线上'); return }
    // 手描兜底:候选线抽不出时,两点定一条轴线
    if (!pending) { setPending({ x: px, y: py }); return }
    if (!label.trim()) { message.warning('请先填写轴号'); setPending(null); return }
    saveSingle({ direction, x1_norm: pending.x, y1_norm: pending.y, x2_norm: px, y2_norm: py })
    setPending(null)
  }

  const saveSingle = async (line: Omit<AxisLineCandidate, 'direction'> & { direction?: string }) => {
    if (!drawingId) return
    try {
      await saveManualAxis(projectId, drawingId, {
        label: label.trim(), direction,
        x1_norm: line.x1_norm, y1_norm: line.y1_norm,
        x2_norm: line.x2_norm, y2_norm: line.y2_norm,
        spacing_to_prev_mm: spacing ?? undefined,
      })
      message.success(`已标定轴线 ${label}`)
      setLabel(''); setSpacing(null)
      reload()
    } catch {
      message.error('标定失败(轴线须近似垂直或水平)')
    }
  }

  const submitBatch = async () => {
    if (!drawingId) return
    if (picked.length === 0) { message.warning('请先在图上点选轴线'); return }
    if (!startLabel.trim()) { message.warning('请填写起始轴号'); return }
    try {
      const r = await saveManualAxesBatch(projectId, drawingId, {
        lines: picked.map((i) => {
          const c = candidates[i]
          return { x1_norm: c.x1_norm, y1_norm: c.y1_norm, x2_norm: c.x2_norm, y2_norm: c.y2_norm }
        }),
        direction,
        start_label: startLabel.trim(),
        end_label: endLabel.trim() || undefined,
        direction_order: order,
      })
      message.success(`已批量标定 ${r.data.saved} 条:${r.data.labels.join('、')}`)
      setPicked([]); setStartLabel(''); setEndLabel('')
      reload()
    } catch (e) {
      const detail = (e as { data?: { detail?: string } })?.data?.detail
      message.error(detail || '批量标定失败')
    }
  }

  /** 多选已标定的单条轴线 → 按起止轴号 + 方向统一重派(旧号先删,不混存) */
  const submitRelabel = async () => {
    if (!drawingId) return
    if (!startLabel.trim()) { message.warning('请填写起始轴号'); return }
    try {
      const r = await relabelManualAxes(projectId, drawingId, {
        labels: selectedLabels, direction,
        start_label: startLabel.trim(),
        end_label: endLabel.trim() || undefined,
        direction_order: order,
      })
      message.success(`已合并重派 ${r.data.relabeled} 条:${r.data.labels.join('、')}`)
      setSelectedLabels([]); setStartLabel(''); setEndLabel('')
      reload()
    } catch (e) {
      const detail = (e as { data?: { detail?: string } })?.data?.detail
      message.error(detail || '合并重派失败')
    }
  }

  /** 找离点击处最近的「既有轴线交点」(自动识别 + 人工标定都算),用于吸附与预填轴号 */
  const nearestIntersection = (
    px: number, py: number,
  ): { x: number; y: number; labelX: string; labelY: string } | null => {
    const all = [
      ...autoAxes.map((a) => ({ label: a.label, direction: a.direction,
        pos: a.direction === 'x' ? (a.x1_norm + a.x2_norm) / 2 : (a.y1_norm + a.y2_norm) / 2 })),
      ...axes.map((a) => ({ label: a.label, direction: a.direction as 'x' | 'y',
        pos: a.direction === 'x' ? (a.x1_norm + a.x2_norm) / 2 : (a.y1_norm + a.y2_norm) / 2 })),
    ]
    const vertical = all.filter((a) => a.direction === 'x')
    const horizontal = all.filter((a) => a.direction === 'y')
    if (!vertical.length || !horizontal.length) return null

    const nearest = (list: typeof vertical, at: number) =>
      list.reduce((best, cur) =>
        Math.abs(cur.pos - at) < Math.abs(best.pos - at) ? cur : best)
    const vx = nearest(vertical, px)
    const hy = nearest(horizontal, py)
    if (Math.abs(vx.pos - px) > SNAP_RADIUS || Math.abs(hy.pos - py) > SNAP_RADIUS) {
      return null
    }
    return { x: vx.pos, y: hy.pos, labelX: vx.label, labelY: hy.label }
  }

  /** 已有交叉点被点中 → 进入编辑(改工程坐标),而不是新建一个重复的 */
  const pointAt2 = (px: number, py: number): AxisIntersection | null => {
    for (const p of points) {
      if (Math.abs(p.x_norm - px) <= SNAP_RADIUS
        && Math.abs(p.y_norm - py) <= SNAP_RADIUS) return p
    }
    return null
  }

  const submitPoint = async (px: number, py: number) => {
    if (!drawingId) return
    try {
      const r = await saveIntersection(projectId, drawingId, {
        label_x: labelX.trim(), label_y: labelY.trim(),
        x_norm: px, y_norm: py,
        world_x: world.x ?? null, world_y: world.y ?? null, world_z: world.z ?? null,
      })
      message.success(
        `已在 ${labelX}轴-${labelY}轴 交叉点生成轴线:${r.data.axes_created.join('、')}`)
      setLabelX(''); setLabelY(''); setWorld({}); setEditingPoint(null)
      reload()
    } catch (e) {
      const detail = (e as { data?: { detail?: string } })?.data?.detail
      message.error(detail || '选点定轴失败')
    }
  }

  const submitMove = async (px: number, py: number) => {
    if (!drawingId || !movingLabel) return
    try {
      await moveManualAxis(projectId, drawingId, {
        label: movingLabel, direction, x_norm: px, y_norm: py,
      })
      message.success(`轴线 ${movingLabel} 已移到新位置`)
      reload()
    } catch {
      message.error('平移失败')
    }
  }

  const checkWorldAnchor = async () => {
    if (!drawingId) return
    const r = await solveWorldAnchor(projectId, drawingId)
    if (!r.success) {
      message.warning('需至少两个填了工程坐标的交叉点才能定位整张图')
      return
    }
    const d = r.data
    message[d.suspect ? 'warning' : 'success'](
      `整图定位:比例 ${d.scale.toFixed(3)} 米/单位 · 旋转 ${d.rotation_deg.toFixed(2)}°`
      + ` · 残差 ${d.rmse_m.toFixed(3)}m`
      + (d.suspect ? '（残差偏大,请核对交叉点是否配错或轴号重名）' : ''))
  }

  const remove = async (axis: ManualAxis) => {
    if (!drawingId) return
    await deleteManualAxis(projectId, drawingId, axis.direction, axis.label)
    reload()
  }

  const deriveScale = async () => {
    if (!drawingId) return
    try {
      const r = await deriveScaleFromAxes(projectId, drawingId)
      message.success(
        `已按轴距反算比例尺:${r.data.scale_m_pt.toFixed(5)} 米/点(${r.data.samples} 组样本)`)
    } catch {
      message.error('需至少两条同向轴线且填写相邻轴距(mm)')
    }
  }

  /** 线 → 覆盖层定位样式(归一化 × 图高) */
  const lineStyle = (
    l: { direction?: string; x1_norm: number; y1_norm: number; x2_norm: number; y2_norm: number },
    isVertical: boolean, color: string, weight: number,
  ): React.CSSProperties => ({
    position: 'absolute',
    left: Math.min(l.x1_norm, l.x2_norm) * imgH,
    top: Math.min(l.y1_norm, l.y2_norm) * imgH,
    width: isVertical ? Math.max(weight / viewScale, 1) : Math.abs(l.x2_norm - l.x1_norm) * imgH,
    height: isVertical ? Math.abs(l.y2_norm - l.y1_norm) * imgH : Math.max(weight / viewScale, 1),
    background: color,
    pointerEvents: 'none',
  })

  return (
    <Modal
      open={!!drawingId}
      title={`标定轴线基准${title ? ' · ' + title : ''}`}
      width={1000}
      onCancel={onClose}
      footer={[
        <Button key="scale" onClick={deriveScale}>按轴距反算比例尺</Button>,
        mode === 'batch' ? (
          <Button key="batch" type="primary" onClick={submitBatch}>
            批量标定 {picked.length} 条
          </Button>
        ) : null,
        <Button key="close" onClick={onClose}>完成</Button>,
      ]}
      destroyOnClose
    >
      <Space style={{ marginBottom: 8 }}>
        <Segmented
          value={mode}
          onChange={(v) => {
            setMode(v as 'single' | 'batch' | 'point' | 'move')
            setPicked([]); setPending(null); setMovingLabel(null)
          }}
          options={[
            { value: 'single', label: '单条标定' },
            { value: 'batch', label: '批量标定' },
            { value: 'point', label: '选点定轴' },
            { value: 'move', label: '平移轴线' },
          ]}
        />
        {mode !== 'point' ? (
          <Radio.Group size="small" value={direction}
            onChange={(e) => setDirection(e.target.value)}>
            <Radio.Button value="x">竖向</Radio.Button>
            <Radio.Button value="y">横向</Radio.Button>
            <Radio.Button value="skew">斜向</Radio.Button>
          </Radio.Group>
        ) : null}
        <Checkbox checked={showAuto} onChange={(e) => setShowAuto(e.target.checked)}>
          显示自动识别轴线({autoAxes.length})
        </Checkbox>
        <Text type="secondary" style={{ fontSize: 12 }}>
          候选线 {visibleCandidates.length} 条,点线即选中
          {memoryCount ? `（含 ${memoryCount} 条橙色为以前手描的记忆）` : ''}
        </Text>
      </Space>

      {mode === 'point' ? (
        <Alert
          type="info" showIcon style={{ marginBottom: 10 }}
          message="点在已有轴线的交点上会自动吸附并填好轴号;点已有交叉点则是改它的坐标"
          description="蓝线是系统自动识别的轴线(带轴号,仅供参考——这些轴号可信度低)。绿点=已填工程坐标的交叉点,橙点=未填;点中即可修改。同一张图有两个带坐标的交叉点,就能把整张图摆进模型坐标系。"
        />
      ) : mode === 'move' ? (
        <Alert
          type="info" showIcon style={{ marginBottom: 10 }}
          message="在下方表格选中要平移的轴线,再点图上目标位置"
          description="自动识别转存的、人工画的轴线都能拖。角度保持不变,只改位置;传的是「移到哪」而非「移了多远」,反复微调不会累积误差。"
        />
      ) : mode === 'single' ? (
        <Alert
          type="info" showIcon style={{ marginBottom: 10 }}
          message="填好轴号,再点图上那条轴线即可标定"
          description="灰色为系统识别出的候选线,点中即吸附;若图上没有候选线,可连点两点手描一条。填「与上一轴距」后可反算比例尺。"
        />
      ) : (
        <Alert
          type="info" showIcon style={{ marginBottom: 10 }}
          message="连续点选多条轴线,只填起止轴号与命名方向,系统自动按序派号"
          description="蓝色为已选中(再点一次取消)。字母轴号自动跳过 I、O、Z;选中条数与起止轴号对不上会报错提示,不会错配。"
        />
      )}

      <Space wrap style={{ marginBottom: 10 }}>
        {mode === 'point' ? (
          <>
            <Input size="small" style={{ width: 96 }} placeholder="竖向轴号 1"
              value={labelX} onChange={(e) => setLabelX(e.target.value)} />
            <Input size="small" style={{ width: 96 }} placeholder="横向轴号 A"
              value={labelY} onChange={(e) => setLabelY(e.target.value)} />
            <InputNumber size="small" style={{ width: 92 }} placeholder="X(米)"
              value={world.x} onChange={(v) => setWorld({ ...world, x: v as number })} />
            <InputNumber size="small" style={{ width: 92 }} placeholder="Y(米)"
              value={world.y} onChange={(v) => setWorld({ ...world, y: v as number })} />
            <InputNumber size="small" style={{ width: 92 }} placeholder="Z(米)"
              value={world.z} onChange={(v) => setWorld({ ...world, z: v as number })} />
            <Button size="small" onClick={checkWorldAnchor}>解算整图定位</Button>
            <Tag>{points.length} 个交叉点</Tag>
            {editingPoint ? (
              <Tag color="processing" closable
                onClose={() => { setEditingPoint(null); setLabelX(''); setLabelY(''); setWorld({}) }}>
                正在改 {editingPoint.label_x}轴-{editingPoint.label_y}轴
              </Tag>
            ) : null}
          </>
        ) : mode === 'move' ? (
          <Tag color={movingLabel ? 'processing' : 'default'}>
            {movingLabel ? `正在平移轴线 ${movingLabel}，请点图上目标位置` : '请在下方表格选中一条轴线'}
          </Tag>
        ) : mode === 'single' ? (
          <>
            <Input size="small" style={{ width: 120 }} placeholder="轴号 如 1 / A / 1-1"
              value={label} onChange={(e) => setLabel(e.target.value)} />
            <InputNumber size="small" style={{ width: 170 }} min={0}
              placeholder="与上一轴距(mm,可选)"
              value={spacing ?? undefined} onChange={(v) => setSpacing(v as number)} />
            {pending ? <Tag color="processing">已取第 1 点,请点第 2 点</Tag> : null}
          </>
        ) : (
          <>
            <Input size="small" style={{ width: 130 }} placeholder="起始轴号 如 1 / A-1"
              value={startLabel} onChange={(e) => setStartLabel(e.target.value)} />
            <Input size="small" style={{ width: 110 }} placeholder="终止轴号(可选)"
              value={endLabel} onChange={(e) => setEndLabel(e.target.value)} />
            <Radio.Group size="small" value={order} onChange={(e) => setOrder(e.target.value)}>
              {ORDER_OPTIONS[direction].map((o) => (
                <Radio.Button key={o.value} value={o.value}>{o.label}</Radio.Button>
              ))}
            </Radio.Group>
            <Tag color={picked.length ? 'blue' : 'default'}>已选 {picked.length} 条</Tag>
          </>
        )}
      </Space>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 50 }}><Spin /></div>
      ) : preview?.kind !== 'image' ? (
        <Alert type="warning" showIcon message="该图纸无法渲染为位图,轴线标定不可用(可下载后本地查看)" />
      ) : (
        <ZoomPanViewer height={460} onScaleChange={setViewScale}>
          <div style={{ position: 'relative', display: 'inline-block' }}>
            <img
              ref={imgRef} src={preview.url} alt="drawing" draggable={false}
              onLoad={() => setImgH(imgRef.current?.clientHeight ?? 0)}
              onMouseDown={(e) => { downAt.current = { x: e.clientX, y: e.clientY } }}
              onClick={handleClick}
              style={{ display: 'block', maxWidth: 'none', cursor: 'crosshair' }}
            />
            {imgH > 0 && visibleCandidates.map(({ c, i }) => (
              // 记忆线(以前人手描过的)用橙色区别于自动检出的灰线
              <div key={`cand-${i}`} style={lineStyle(
                c, c.direction === 'x',
                picked.includes(i) ? '#1677ff'
                  : c.from_memory ? 'rgba(250,140,22,0.75)' : 'rgba(140,140,140,0.55)',
                picked.includes(i) ? 3 : 1.5)} />
            ))}
            {imgH > 0 && axes.map((a) => (
              <div key={`${a.direction}-${a.label}`}
                style={lineStyle(a, a.direction === 'x', '#f5222d', 2)} />
            ))}
            {imgH > 0 && showAuto && autoAxes.map((a, i) => {
              const isX = a.direction === 'x'
              const pos = (isX ? (a.x1_norm + a.x2_norm) : (a.y1_norm + a.y2_norm)) / 2
              return (
                // 自动识别的轴线:蓝色细线 + 轴号,仅作参考(这些轴号可信度低)
                <div key={`auto-${i}-${a.label}`}>
                  <div style={{
                    position: 'absolute',
                    left: isX ? pos * imgH : 0,
                    top: isX ? 0 : pos * imgH,
                    width: isX ? Math.max(1 / viewScale, 0.5) : '100%',
                    height: isX ? '100%' : Math.max(1 / viewScale, 0.5),
                    background: 'rgba(22,119,255,0.45)', pointerEvents: 'none',
                  }} />
                  <span style={{
                    position: 'absolute',
                    left: isX ? pos * imgH + 2 : 2,
                    top: isX ? 2 : pos * imgH + 2,
                    fontSize: Math.max(10 / viewScale, 5),
                    color: '#1677ff', background: 'rgba(255,255,255,0.75)',
                    padding: '0 1px', pointerEvents: 'none', lineHeight: 1,
                  }}>{a.label}</span>
                </div>
              )
            })}
            {imgH > 0 && points.map((p) => (
              // 交叉点:绿点 + 轴号对,带工程坐标的额外标出
              <div key={`${p.label_x}-${p.label_y}`} style={{
                position: 'absolute',
                left: p.x_norm * imgH - 4, top: p.y_norm * imgH - 4,
                width: 8, height: 8, borderRadius: 4,
                background: p.world_x != null ? '#52c41a' : '#faad14',
                boxShadow: editingPoint
                  && editingPoint.label_x === p.label_x
                  && editingPoint.label_y === p.label_y
                  ? '0 0 0 3px #1677ff' : '0 0 0 2px #fff',
                pointerEvents: 'none',
              }} />
            ))}
          </div>
        </ZoomPanViewer>
      )}

      {selectedLabels.length ? (
        <Space style={{ marginTop: 10 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            已选 {selectedLabels.length} 条 —— 填上方「起始/终止轴号 + 命名方向」后
          </Text>
          <Button size="small" type="primary" onClick={submitRelabel}>
            合并重新派号
          </Button>
          <Button size="small" onClick={() => setSelectedLabels([])}>取消选择</Button>
        </Space>
      ) : null}

      {mode === 'point' && points.length ? (
        <Table<AxisIntersection>
          style={{ marginTop: 10 }} size="small"
          rowKey={(r) => `${r.label_x}-${r.label_y}`}
          pagination={false} dataSource={points}
          columns={[
            { title: '交叉点', width: 110,
              render: (_: unknown, r) => <Tag color="green">{r.label_x}轴-{r.label_y}轴</Tag> },
            { title: '工程坐标(米)', width: 200,
              render: (_: unknown, r) => (r.world_x != null
                ? `${r.world_x}, ${r.world_y}, ${r.world_z ?? '-'}`
                : <Text type="secondary">未填</Text>) },
            { title: '', width: 60,
              render: (_: unknown, r) => (
                <Button size="small" type="link" danger onClick={async () => {
                  if (!drawingId) return
                  await deleteIntersection(projectId, drawingId, r.label_x, r.label_y)
                  reload()
                }}>删除</Button>
              ) },
          ]}
        />
      ) : null}

      <Table<ManualAxis>
        style={{ marginTop: 10 }}
        size="small" rowKey={(r) => `${r.direction}-${r.label}`}
        rowSelection={mode === 'move' ? {
          type: 'radio',
          selectedRowKeys: movingLabel ? [`${direction}-${movingLabel}`] : [],
          onChange: (_keys, rows) => setMovingLabel(rows[0]?.label ?? null),
          getCheckboxProps: (r) => ({ disabled: r.direction !== direction }),
        } : {
          selectedRowKeys: selectedLabels.map((l) => `${direction}-${l}`),
          onChange: (_keys, rows) => setSelectedLabels(rows.map((r) => r.label)),
          getCheckboxProps: (r) => ({ disabled: r.direction !== direction }),
        }}
        pagination={{ pageSize: 8, size: 'small', hideOnSinglePage: true }}
        dataSource={axes}
        locale={{ emptyText: '尚未标定轴线' }}
        columns={[
          { title: '轴号', dataIndex: 'label', width: 80,
            render: (v: string) => <Tag color="red">{v}</Tag> },
          { title: '方向', dataIndex: 'direction', width: 100,
            render: (v: string) => (v === 'x' ? '竖向' : v === 'y' ? '横向' : '斜向') },
          { title: '与上一轴距(mm)', dataIndex: 'spacing_to_prev_mm', width: 150,
            render: (v: number | null) => (v ? v : <Text type="secondary">—</Text>) },
          { title: '', width: 60,
            render: (_: unknown, row) => (
              <Button size="small" type="link" danger onClick={() => remove(row)}>删除</Button>
            ) },
        ]}
      />
    </Modal>
  )
}
