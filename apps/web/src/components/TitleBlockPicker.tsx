/**
 * 图框字段框选 —— 在图纸上框出「专业/图号/图名」所在区域,读值并记成模板。
 *
 * 解决的是「标签本身没被 OCR 出来」的图纸(实测 140 张):按标签找值彻底读不到,
 * 但人一眼能看见那个格子。框一次即可:
 *   ① 立刻读出这张图的值 → ② 记成模板 → ③ 一键套用到同版式其他未读到的图纸。
 *
 * 模板按**页面宽高比**分桶匹配同版式图框;可选存为**跨项目全局记忆**,
 * 让别的项目遇到同样图框时直接受益。
 *
 * **交互分两态**(拖拽只有一个,不能既平移又框选):
 * - 浏览态:拖拽平移、滚轮缩放,先把要框的那格找到并放大;
 * - 框选态:点「开始框选」进入,拖拽画框 → 松手停住不提交 → 核对无误再点「确认读取」。
 *   框歪了可以重画,或退回浏览态挪一下位置。
 */
import { useEffect, useRef, useState } from 'react'
import {
  Alert, AutoComplete, Button, Checkbox, Modal, Radio, Space, Spin, Tag,
  Typography, message,
} from 'antd'
import {
  applyTitleBlockTemplates, getApplyStatus, getDrawingPreview, readTitleBlockRegion,
  type ApplyResult, type DrawingPreview, type TitleBlockField,
} from '@/services/projectInfo'
import ZoomPanViewer from './ZoomPanViewer'

const { Text } = Typography

/** 后台任务轮询:间隔与放弃等待的上限 */
const POLL_INTERVAL_MS = 3000
const POLL_TIMEOUT_MS = 10 * 60 * 1000

const FIELD_OPTIONS: { value: TitleBlockField; label: string }[] = [
  { value: 'discipline', label: '专业' },
  { value: 'drawing_no', label: '图号' },
  { value: 'title', label: '图名' },
]

interface TitleBlockPickerProps {
  projectId: string
  drawingId: string | null
  title?: string
  onClose: () => void
  /** 读到值后回调,便于外层刷新列表 */
  onUpdated?: (field: TitleBlockField, value: string) => void
}

interface Rect { x1: number; y1: number; x2: number; y2: number }

export default function TitleBlockPicker({
  projectId, drawingId, title, onClose, onUpdated,
}: TitleBlockPickerProps) {
  const [preview, setPreview] = useState<DrawingPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [imgH, setImgH] = useState(0)
  const [field, setField] = useState<TitleBlockField>('discipline')
  const [globalMemory, setGlobalMemory] = useState(false)
  const [rect, setRect] = useState<Rect | null>(null)
  const [dragging, setDragging] = useState(false)
  // 框选态:拖拽画框而非平移。松手只停住,不自动提交——先让人看清框对没框对。
  const [selecting, setSelecting] = useState(false)
  const [value, setValue] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [applied, setApplied] = useState<ApplyResult | null>(null)
  // 自动识别糊掉时:把区域原文摆给人,人拍板
  const [confirm, setConfirm] = useState<{ raw: string; rect: Rect } | null>(null)
  const [manual, setManual] = useState('')
  const imgRef = useRef<HTMLImageElement>(null)

  useEffect(() => {
    if (!drawingId) return
    setLoading(true)
    setPreview(null); setRect(null); setValue(null); setApplied(null); setImgH(0)
    setSelecting(false); setConfirm(null)
    getDrawingPreview(drawingId, { raster: true })
      .then(setPreview)
      .catch(() => message.error('图纸预览加载失败'))
      .finally(() => setLoading(false))
  }, [drawingId])

  /** 归一化坐标:同除图片显示高度,与后端 page_h 口径一致 */
  const pointAt = (e: React.MouseEvent<HTMLImageElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    return {
      x: (e.clientX - box.left) / box.height,
      y: (e.clientY - box.top) / box.height,
    }
  }

  const submit = async (r: Rect, override?: string) => {
    if (!drawingId) return
    try {
      const res = await readTitleBlockRegion(projectId, drawingId, {
        field, ...r, remember: true, global_memory: globalMemory,
        value: override,
      })
      if (res.error === 'NEEDS_CONFIRMATION') {
        // 不猜:识别出原文但校验不过(如「建筑」被认成「建 个人」),交人拍板
        setConfirm({ raw: res.data.raw_text, rect: r })
        setManual('')
        return
      }
      const got = res.data.value ?? ''
      setValue(got)
      setConfirm(null)
      setSelecting(false)
      message.success(`读到「${got}」,已记住该区域,正在刷新同版式图纸…`)
      onUpdated?.(field, got)
      runApply()          // 框选完就自动刷同类图纸,不必再点一次按钮
    } catch (e) {
      const detail = (e as { data?: { detail?: string } })?.data?.detail
      message.error(detail || '读取失败,请重新框选')
    }
  }

  /** 批量套用是后台任务:入队后轮询,别让浏览器干等几分钟 */
  const runApply = async () => {
    setApplying(true)
    setApplied(null)
    try {
      const { data } = await applyTitleBlockTemplates(projectId, field)
      message.info('已在后台开始刷新同版式图纸,完成后会提示')
      const started = Date.now()
      const poll = async (): Promise<void> => {
        const s = await getApplyStatus(projectId, data.task_id)
        if (s.state === 'SUCCESS' && s.data) {
          setApplied(s.data)
          setApplying(false)
          if (s.data.updated) {
            message.success(
              `已补全 ${s.data.updated}/${s.data.candidates} 张`
              + (s.data.ocr_skipped ? `,另有 ${s.data.ocr_skipped} 张未试,可再点一次` : ''))
          } else {
            message.warning(
              `同版式图纸一张也没补上(候选 ${s.data.candidates} 张)——`
              + '多半是这些图的图框版式不同,请到其中一张上再框一次')
          }
          return
        }
        if (s.state === 'FAILURE') {
          setApplying(false)
          message.error(`批量刷新失败:${s.error ?? '未知错误'}`)
          return
        }
        if (Date.now() - started > POLL_TIMEOUT_MS) {
          setApplying(false)
          message.warning('后台仍在跑,可稍后刷新页面查看结果')
          return
        }
        setTimeout(poll, POLL_INTERVAL_MS)
      }
      poll()
    } catch {
      setApplying(false)
      message.error('批量刷新入队失败')
    }
  }

  /** 框太小说明只是点了一下,不是有效矩形 */
  const isRectUsable = !!rect
    && Math.abs(rect.x2 - rect.x1) >= 0.002
    && Math.abs(rect.y2 - rect.y1) >= 0.002

  const overlay = rect && imgH > 0 ? {
    position: 'absolute' as const,
    left: Math.min(rect.x1, rect.x2) * imgH,
    top: Math.min(rect.y1, rect.y2) * imgH,
    width: Math.abs(rect.x2 - rect.x1) * imgH,
    height: Math.abs(rect.y2 - rect.y1) * imgH,
    border: '2px solid #1677ff',
    background: 'rgba(22,119,255,0.12)',
    pointerEvents: 'none' as const,
  } : null

  return (
    <Modal
      open={!!drawingId}
      title={`框选图框字段${title ? ' · ' + title : ''}`}
      width={1000}
      onCancel={onClose}
      footer={[
        <Button key="apply" loading={applying} onClick={runApply}>
          再刷一批同版式图纸
        </Button>,
        <Button key="close" type="primary" onClick={onClose}>完成</Button>,
      ]}
      destroyOnClose
    >
      <Alert
        type="info" showIcon style={{ marginBottom: 10 }}
        message={selecting
          ? '框选中:拖拽画框 → 松手停住 → 核对无误点「确认读取」'
          : '先拖拽平移 / 滚轮放大找到图框,再点「开始框选」'}
        description={selecting
          ? '此时拖拽不再平移图纸。框歪了点「重新框」,想挪位置点「退出框选」。滚轮缩放仍可用。标签(如「专业」「DRAWING NO.」)会自动剔除,框大一点没关系。'
          : '读到值后系统会记住这个区域,可一键套用到同版式的其他图纸,并可存为跨项目记忆。'}
      />
      <Space wrap style={{ marginBottom: 10 }}>
        <Radio.Group size="small" value={field}
          onChange={(e) => { setField(e.target.value); setValue(null); setApplied(null) }}>
          {FIELD_OPTIONS.map((o) => (
            <Radio.Button key={o.value} value={o.value}>{o.label}</Radio.Button>
          ))}
        </Radio.Group>
        <Checkbox checked={globalMemory} onChange={(e) => setGlobalMemory(e.target.checked)}>
          存为跨项目记忆
        </Checkbox>
        {selecting ? (
          <>
            <Button size="small" onClick={() => { setSelecting(false); setRect(null) }}>
              退出框选(可平移)
            </Button>
            <Button
              size="small" type="primary" disabled={!isRectUsable}
              onClick={() => rect && submit(rect)}
            >
              确认读取
            </Button>
            {rect ? (
              <Button size="small" onClick={() => setRect(null)}>重新框</Button>
            ) : null}
          </>
        ) : (
          <Button size="small" type="primary" onClick={() => setSelecting(true)}>
            开始框选
          </Button>
        )}
        {value ? <Tag color="green">读到:{value}</Tag> : null}
        {applied ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            已补全 {applied.updated}/{applied.candidates} 张
            {applied.ocr_skipped ? `,${applied.ocr_skipped} 张待续` : ''}
          </Text>
        ) : null}
      </Space>

      {confirm ? (
        <Alert
          type="warning" showIcon style={{ marginBottom: 10 }}
          message={`区域原文「${confirm.raw || '(空)'}」未能自动判定,请确认正确值`}
          description={
            <Space style={{ marginTop: 6 }}>
              <AutoComplete
                style={{ width: 200 }} value={manual} onChange={setManual}
                placeholder={field === 'discipline' ? '如 建筑 / 给排水' : '填写正确值'}
                options={field === 'discipline'
                  ? ['建筑', '结构', '给排水', '暖通', '电气', '基坑围护', '幕墙', '精装']
                      .map((v) => ({ value: v }))
                  : []}
              />
              <Button type="primary" size="small" disabled={!manual.trim()}
                onClick={() => submit(confirm.rect, manual.trim())}>
                确认并记住
              </Button>
              <Button size="small" onClick={() => setConfirm(null)}>重新框选</Button>
            </Space>
          }
        />
      ) : null}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 50 }}><Spin /></div>
      ) : preview?.kind !== 'image' ? (
        <Alert type="warning" showIcon message="该图纸无法渲染为位图,框选不可用(可下载后本地查看)" />
      ) : (
        <ZoomPanViewer height={480} panDisabled={selecting}>
          <div style={{ position: 'relative', display: 'inline-block' }}>
            <img
              ref={imgRef} src={preview.url} alt="drawing" draggable={false}
              onLoad={() => setImgH(imgRef.current?.clientHeight ?? 0)}
              onMouseDown={(e) => {
                if (!selecting) return          // 浏览态:交给 ZoomPanViewer 平移
                e.preventDefault()
                const p = pointAt(e)
                setRect({ x1: p.x, y1: p.y, x2: p.x, y2: p.y })
                setDragging(true)
              }}
              onMouseMove={(e) => {
                if (!dragging) return
                const p = pointAt(e)
                setRect((prev) => (prev ? { ...prev, x2: p.x, y2: p.y } : prev))
              }}
              onMouseUp={(e) => {
                if (!dragging) return
                setDragging(false)
                const p = pointAt(e)
                // 松手只停住框,**不提交**:先让人看清框对没框对,再点「确认读取」
                setRect((prev) => (prev ? { ...prev, x2: p.x, y2: p.y } : prev))
              }}
              style={{
                display: 'block', maxWidth: 'none',
                cursor: selecting ? 'crosshair' : 'grab',
              }}
            />
            {overlay ? <div style={overlay} /> : null}
          </div>
        </ZoomPanViewer>
      )}
    </Modal>
  )
}
