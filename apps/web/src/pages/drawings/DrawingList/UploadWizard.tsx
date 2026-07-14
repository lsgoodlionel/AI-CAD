import { useState } from 'react'
import { useNavigate } from '@umijs/max'
import { Alert, Checkbox, Form, Input, InputNumber, Modal, Select, Table, Upload, message } from 'antd'
import type { TableProps, UploadFile } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import {
  batchUploadDrawings, createReviewBatch, importDrawingsZip, uploadDrawing,
} from '@/services/drawings'
import type { BatchUploadResult, CreateReviewBatchResult, ZipImportResult } from '@/services/drawings'

const { Dragger } = Upload

export const DISCIPLINE_OPTIONS = [
  { label: '结构', value: 'structure' },
  { label: '建筑', value: 'architecture' },
  { label: '机电', value: 'mep' },
  { label: '幕墙', value: 'curtain_wall' },
  { label: '精装', value: 'decoration' },
  { label: '其他', value: 'other' },
]

export const DISCIPLINE_LABEL: Record<string, string> = Object.fromEntries(
  DISCIPLINE_OPTIONS.map(({ value, label }) => [value, label])
)

/** 从未知错误中提取后端 detail/error 文案 */
export function extractErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const resp = (error as { response?: { data?: { detail?: string; error?: string } } }).response
    return resp?.data?.detail ?? resp?.data?.error ?? fallback
  }
  return fallback
}

/** 上传弹窗中每个待上传文件的可编辑元数据行（ZIP 模式不使用，由后端逐条目解析） */
interface UploadMetaRow {
  uid: string
  filename: string
  drawing_no: string
  discipline: string
  version: string
  title: string
}

/** 与后端 services/drawing_filename_parser.py 同规则的前端简版：图号首个匹配 */
const DRAWING_NO_RE = /[A-Za-z一-龥]{1,4}[-_ ]?\d{1,4}/

/** 文件名预解析：专业前缀 + 图号，解析不出的字段给安全默认值 */
function parseFilenameMeta(filename: string): Omit<UploadMetaRow, 'uid' | 'filename'> {
  const stem = filename.replace(/\.[^.]+$/, '')
  let discipline = 'other'
  if (/结施|GS/i.test(stem)) discipline = 'structure'
  else if (/建施|JS/i.test(stem)) discipline = 'architecture'
  else if (/水施|电施|暖施/.test(stem)) discipline = 'mep'
  else if (/装施/.test(stem)) discipline = 'decoration'
  const noMatch = stem.match(DRAWING_NO_RE)
  return {
    drawing_no: noMatch ? noMatch[0] : stem,
    discipline,
    version: 'A',
    title: stem,
  }
}

const isZipFile = (filename: string): boolean => filename.toLowerCase().endsWith('.zip')

/** 上传分流模式：单张 / 批量 / ZIP 整套导入，均由所选文件自动判定，无需用户手动选择 */
type UploadMode = 'empty' | 'single' | 'batch' | 'zip' | 'mixed'

function detectUploadMode(fl: UploadFile[]): UploadMode {
  if (fl.length === 0) return 'empty'
  const zipCount = fl.filter((f) => isZipFile(f.name)).length
  const normalCount = fl.length - zipCount
  if (zipCount > 0 && normalCount > 0) return 'mixed'
  if (zipCount > 1) return 'mixed'
  if (zipCount === 1) return 'zip'
  return normalCount === 1 ? 'single' : 'batch'
}

function getModeErrorText(fl: UploadFile[]): string | null {
  const zipCount = fl.filter((f) => isZipFile(f.name)).length
  const normalCount = fl.length - zipCount
  if (zipCount > 0 && normalCount > 0) {
    return '不能同时选择 ZIP 整套包与普通图纸文件，请分开上传'
  }
  if (zipCount > 1) {
    return '一次只能导入一个 ZIP 整套包'
  }
  return null
}

interface ProjectSelectOption {
  label: string
  value: string
}

interface UploadWizardProps {
  open: boolean
  projectSelectOptions: ProjectSelectOption[]
  onClose: () => void
  /** 上传（含批量/ZIP）成功后触发，用于刷新图纸列表 */
  onUploaded: () => void
}

/**
 * 统一上传向导（Phase D · D-02）：单张 / 多文件批量 / ZIP 整套导入合并为一个拖拽入口，
 * 按所选文件自动分流，无需用户预先理解三种模式的差异。
 */
export default function UploadWizard({
  open, projectSelectOptions, onClose, onUploaded,
}: UploadWizardProps) {
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [uploading, setUploading] = useState(false)
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [metaRows, setMetaRows] = useState<UploadMetaRow[]>([])
  const [autoBatch, setAutoBatch] = useState(false)

  const mode = detectUploadMode(fileList)
  const modeErrorText = getModeErrorText(fileList)

  const syncMetaRows = (fl: UploadFile[]) => {
    setFileList(fl)
    const normalFiles = fl.filter((f) => !isZipFile(f.name))
    setMetaRows((prev) =>
      normalFiles.map(
        (f) =>
          prev.find((r) => r.uid === f.uid) ?? {
            uid: f.uid,
            filename: f.name,
            ...parseFilenameMeta(f.name),
          }
      )
    )
  }

  const updateMetaRow = (uid: string, field: keyof UploadMetaRow, value: string) => {
    setMetaRows((prev) => prev.map((r) => (r.uid === uid ? { ...r, [field]: value } : r)))
  }

  const resetState = () => {
    form.resetFields()
    setFileList([])
    setMetaRows([])
    setAutoBatch(false)
  }

  const handleCancel = () => {
    resetState()
    onClose()
  }

  const maybeCreateReviewBatch = async (projectId: string, drawingIds: string[]) => {
    if (!autoBatch || drawingIds.length === 0) return null
    try {
      const res: CreateReviewBatchResult = await createReviewBatch({
        project_id: projectId,
        drawing_ids: drawingIds,
      })
      message.success(`已创建套图审查批次，共 ${res.total} 张图纸`)
      return res.batch_id
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, '创建套图审查批次失败，图纸已上传成功'))
      return null
    }
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    if (mode === 'empty') {
      message.error('请选择图纸文件')
      return
    }
    if (mode === 'mixed') {
      message.error(modeErrorText ?? '文件选择有误，请检查')
      return
    }
    if (mode === 'single' || mode === 'batch') {
      const missingNo = metaRows.find((r) => !r.drawing_no.trim())
      if (missingNo) {
        message.error(`请填写文件「${missingNo.filename}」的图号`)
        return
      }
    }

    setUploading(true)
    try {
      const projectId = values.project_id as string
      let createdIds: string[] = []

      if (mode === 'zip') {
        const file = fileList[0]?.originFileObj
        if (!file) {
          message.error('文件读取失败，请重新选择')
          return
        }
        const fd = new FormData()
        fd.append('project_id', projectId)
        fd.append('auto_review', 'true')
        fd.append('file', file)
        const res: ZipImportResult = await importDrawingsZip(fd)
        createdIds = res.created.map((c) => c.drawing_id)
        if (res.failed.length || res.skipped.length) {
          message.warning(
            `整套导入完成：成功 ${res.created.length} 张，失败 ${res.failed.length} 张，` +
            `跳过 ${res.skipped.length} 个非图纸文件`
          )
        } else {
          message.success(`整套导入完成，共 ${res.created.length} 张图纸，触发 ${res.review_triggered} 个 AI 审图任务`)
        }
      } else if (mode === 'single') {
        const row = metaRows[0]
        const file = fileList[0]?.originFileObj
        if (!file) {
          message.error('文件读取失败，请重新选择')
          return
        }
        const fd = new FormData()
        fd.append('project_id', projectId)
        fd.append('drawing_no', row.drawing_no.trim())
        fd.append('discipline', row.discipline)
        fd.append('version', row.version.trim() || 'A')
        fd.append('title', row.title)
        if (values.estimated_impact) {
          fd.append('estimated_impact', String(values.estimated_impact))
        }
        fd.append('file', file)
        const res = await uploadDrawing(fd)
        createdIds = [res.drawing_id]
        message.success('图纸已上传，AI 审图任务已触发')
      } else {
        const fd = new FormData()
        fd.append('project_id', projectId)
        fd.append(
          'items_meta',
          JSON.stringify(
            metaRows.map(({ filename, drawing_no, discipline, version, title }) => ({
              filename,
              drawing_no: drawing_no.trim(),
              discipline,
              version: version.trim() || 'A',
              title,
            }))
          )
        )
        for (const f of fileList) {
          if (f.originFileObj) fd.append('files', f.originFileObj)
        }
        const res: BatchUploadResult = await batchUploadDrawings(fd)
        createdIds = res.created.map((c) => c.drawing_id)
        if (res.failed.length) {
          message.warning(
            `成功 ${res.created.length} 张，失败 ${res.failed.length} 张：` +
            res.failed.map((x) => `${x.filename}（${x.error}）`).join('、')
          )
        } else {
          message.success(`已上传 ${res.created.length} 张图纸，触发 ${res.review_triggered} 个 AI 审图任务`)
        }
      }

      onUploaded()
      const batchId = await maybeCreateReviewBatch(projectId, createdIds)
      resetState()
      onClose()
      if (batchId) {
        navigate(`/drawings/review-batches/${batchId}`)
      }
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, '上传失败'))
    } finally {
      setUploading(false)
    }
  }

  const metaColumns: TableProps<UploadMetaRow>['columns'] = [
    { title: '文件名', dataIndex: 'filename', width: 180, ellipsis: true },
    {
      title: '图号',
      dataIndex: 'drawing_no',
      width: 130,
      render: (_, row) => (
        <Input
          size="small"
          value={row.drawing_no}
          onChange={(e) => updateMetaRow(row.uid, 'drawing_no', e.target.value)}
        />
      ),
    },
    {
      title: '专业',
      dataIndex: 'discipline',
      width: 110,
      render: (_, row) => (
        <Select
          size="small"
          style={{ width: '100%' }}
          options={DISCIPLINE_OPTIONS}
          value={row.discipline}
          onChange={(v: string) => updateMetaRow(row.uid, 'discipline', v)}
        />
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      width: 70,
      render: (_, row) => (
        <Input
          size="small"
          value={row.version}
          onChange={(e) => updateMetaRow(row.uid, 'version', e.target.value)}
        />
      ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      render: (_, row) => (
        <Input
          size="small"
          value={row.title}
          onChange={(e) => updateMetaRow(row.uid, 'title', e.target.value)}
        />
      ),
    },
  ]

  return (
    <Modal
      title="智能上传图纸"
      open={open}
      onCancel={handleCancel}
      onOk={handleSubmit}
      confirmLoading={uploading}
      width={860}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item name="project_id" label="所属项目" rules={[{ required: true }]}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="选择项目"
            options={projectSelectOptions}
          />
        </Form.Item>

        <Form.Item label="图纸文件" required>
          <Dragger
            accept=".pdf,.dwg,.dxf,.ifc,.zip"
            multiple
            fileList={fileList}
            beforeUpload={() => false}
            onChange={({ fileList: fl }) => syncMetaRows(fl)}
            onRemove={(file) => syncMetaRows(fileList.filter((f) => f.uid !== file.uid))}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
            <p className="ant-upload-hint">
              支持 PDF / DWG / DXF / IFC 单张或多张图纸，或 ZIP 整套压缩包（单文件 ≤200MB）；
              系统自动识别上传类型并预填元数据
            </p>
          </Dragger>
        </Form.Item>

        {mode === 'single' && (
          <Alert type="success" showIcon style={{ marginBottom: 16 }} message="已识别为单张图纸上传" />
        )}
        {mode === 'batch' && (
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 16 }}
            message={`已识别为批量上传，共 ${fileList.length} 个文件，请核对下方元数据`}
          />
        )}
        {mode === 'zip' && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="已识别为 ZIP 整套导入"
            description="上传后将解压并按文件名自动解析每张图纸的图号、专业等信息，无需逐张填写"
          />
        )}
        {mode === 'mixed' && (
          <Alert type="error" showIcon style={{ marginBottom: 16 }} message={modeErrorText} />
        )}

        {mode === 'single' && (
          <Form.Item name="estimated_impact" label="预估影响金额（元）">
            <InputNumber style={{ width: '100%' }} min={0} step={10000} />
          </Form.Item>
        )}

        {(mode === 'single' || mode === 'batch') && metaRows.length > 0 && (
          <Table<UploadMetaRow>
            size="small"
            rowKey="uid"
            columns={metaColumns}
            dataSource={metaRows}
            pagination={false}
            scroll={{ y: 280 }}
            style={{ marginBottom: 16 }}
          />
        )}

        {mode !== 'empty' && mode !== 'mixed' && (
          <Form.Item style={{ marginBottom: 0 }}>
            <Checkbox checked={autoBatch} onChange={(e) => setAutoBatch(e.target.checked)}>
              上传完成后自动创建套图审查批次
            </Checkbox>
          </Form.Item>
        )}
      </Form>
    </Modal>
  )
}
