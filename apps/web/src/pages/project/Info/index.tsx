/**
 * 工程信息页(路由 /project-info 与 /project-info/:projectId,Phase E1-3)
 *
 * 聚合全部图纸抽取信息(文字说明/设计说明/标注/标高/轴线/图签等),
 * 每条信息强制携带来源图纸并可打开预览——「信息 → 源图纸」溯源闭环。
 *
 * - 无 projectId:项目选择卡片(照 model/ProjectModel 模式)
 * - 有 projectId:类别侧栏 + 明细表 + 覆盖率 + 重新抽取
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { history, useParams } from '@umijs/max'
import {
  Alert, Button, Card, Col, Empty, Input, Menu, Progress, Row, Select,
  Space, Spin, Table, Tag, Tooltip, Typography, message,
} from 'antd'
import { EyeOutlined, SyncOutlined } from '@ant-design/icons'
import { listProjects } from '@/services/projects'
import {
  INFO_CATEGORY_LABEL,
  INFO_EXTRACTOR_LABEL,
  getInfoSummary,
  listInfoItems,
  triggerInfoExtract,
} from '@/services/projectInfo'
import type { InfoItem, InfoSummary } from '@/services/projectInfo'
import DrawingPreviewModal from '@/components/DrawingPreviewModal'

const { Text, Title } = Typography

const DISCIPLINE_LABEL: Record<string, string> = {
  architecture: '建筑',
  structure: '结构',
  mep: '机电',
  decoration: '装修',
  general: '通用',
}

const PAGE_SIZE = 50

interface ProjectOption {
  id: string
  name: string
  code?: string
}

function ProjectPicker() {
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listProjects({ limit: 200 })
      .then((res: { items?: ProjectOption[] }) => setProjects(res.items ?? []))
      .catch(() => message.error('项目列表加载失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin tip="加载项目列表…" />
      </div>
    )
  }
  if (projects.length === 0) {
    return <Empty style={{ marginTop: 80 }} description="暂无项目" />
  }
  return (
    <div style={{ padding: 16 }}>
      <Title level={5}>选择项目查看工程信息</Title>
      <Row gutter={[16, 16]}>
        {projects.map((project) => (
          <Col key={project.id} xs={24} sm={12} md={8} lg={6}>
            <Card
              hoverable
              size="small"
              onClick={() => history.push(`/project-info/${project.id}`)}
            >
              <Space direction="vertical" size={4}>
                <Text strong>{project.name}</Text>
                {project.code ? <Text type="secondary">{project.code}</Text> : null}
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  )
}

function InfoWorkspace({ projectId }: { projectId: string }) {
  const [summary, setSummary] = useState<InfoSummary | null>(null)
  const [category, setCategory] = useState<string>('')
  const [discipline, setDiscipline] = useState<string>('')
  const [keyword, setKeyword] = useState('')
  const [items, setItems] = useState<InfoItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [preview, setPreview] = useState<{ id: string; title: string } | null>(null)

  const loadSummary = useCallback(() => {
    getInfoSummary(projectId)
      .then(setSummary)
      .catch(() => message.error('工程信息概要加载失败'))
  }, [projectId])

  const loadItems = useCallback(() => {
    setLoading(true)
    listInfoItems(projectId, {
      category: category || undefined,
      discipline: discipline || undefined,
      q: keyword || undefined,
      page,
      page_size: PAGE_SIZE,
    })
      .then((res) => {
        setItems(res.items)
        setTotal(res.total)
      })
      .catch(() => message.error('工程信息明细加载失败'))
      .finally(() => setLoading(false))
  }, [projectId, category, discipline, keyword, page])

  useEffect(() => loadSummary(), [loadSummary])
  useEffect(() => loadItems(), [loadItems])

  const handleExtract = async () => {
    setExtracting(true)
    try {
      await triggerInfoExtract(projectId)
      message.success('重抽任务已入队，抽取完成后刷新本页即可看到最新信息')
    } catch {
      message.error('触发重抽失败')
    } finally {
      setExtracting(false)
    }
  }

  const coverage = summary?.coverage
  const coveragePercent = coverage && coverage.total_drawings > 0
    ? Math.round((coverage.extracted_drawings / coverage.total_drawings) * 100)
    : 0

  const menuItems = useMemo(() => {
    const cats = summary?.categories ?? []
    return [
      { key: '', label: `全部（${cats.reduce((acc, c) => acc + c.count, 0)}）` },
      ...cats.map((c) => ({
        key: c.category,
        label: `${INFO_CATEGORY_LABEL[c.category] ?? c.category}（${c.count}）`,
      })),
    ]
  }, [summary])

  const columns = [
    {
      title: '内容',
      dataIndex: 'content',
      ellipsis: true,
      render: (v: string, row: InfoItem) => (
        <Tooltip title={v}>
          <Text>{v}</Text>
          {row.value_json ? (
            <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
              {JSON.stringify(row.value_json)}
            </Text>
          ) : null}
        </Tooltip>
      ),
    },
    {
      title: '类别',
      dataIndex: 'category',
      width: 110,
      render: (v: string) => <Tag>{INFO_CATEGORY_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '专业',
      dataIndex: 'discipline',
      width: 80,
      render: (v: string) => DISCIPLINE_LABEL[v] ?? v,
    },
    {
      title: '来源',
      dataIndex: 'extractor',
      width: 110,
      render: (v: string, row: InfoItem) => (
        <Space direction="vertical" size={0}>
          <Text style={{ fontSize: 12 }}>{INFO_EXTRACTOR_LABEL[v] ?? v}</Text>
          {row.confidence != null ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              置信 {(row.confidence * 100).toFixed(0)}%
            </Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: '来源图纸',
      dataIndex: 'drawing_no',
      width: 220,
      render: (_: unknown, row: InfoItem) => (
        <Button
          type="link"
          size="small"
          icon={<EyeOutlined />}
          onClick={() => setPreview({
            id: row.drawing_id,
            title: `${row.drawing_no} ${row.drawing_title}`,
          })}
        >
          {row.drawing_no} {row.drawing_title}
        </Button>
      ),
    },
  ]

  return (
    <div style={{ padding: 16 }}>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap size="large">
          <Title level={5} style={{ margin: 0 }}>工程信息</Title>
          {coverage ? (
            <Space>
              <Text type="secondary">
                抽取覆盖 {coverage.extracted_drawings}/{coverage.total_drawings} 张图纸
              </Text>
              <Progress
                percent={coveragePercent}
                size="small"
                style={{ width: 160 }}
              />
            </Space>
          ) : null}
          <Button
            icon={<SyncOutlined />}
            loading={extracting}
            onClick={handleExtract}
          >
            重新抽取全部图纸
          </Button>
        </Space>
        {coverage && coverage.extracted_drawings === 0 ? (
          <Alert
            style={{ marginTop: 8 }}
            type="info"
            showIcon
            message="尚未抽取任何图纸信息，点击「重新抽取全部图纸」开始（大项目需数分钟至数十分钟）"
          />
        ) : null}
      </Card>

      <Row gutter={12}>
        <Col flex="220px">
          <Card size="small" bodyStyle={{ padding: 0 }}>
            <Menu
              mode="inline"
              selectedKeys={[category]}
              items={menuItems}
              onClick={({ key }) => {
                setCategory(key)
                setPage(1)
              }}
            />
          </Card>
        </Col>
        <Col flex="auto">
          <Card size="small">
            <Space style={{ marginBottom: 12 }} wrap>
              <Select
                placeholder="按专业筛选"
                allowClear
                style={{ width: 140 }}
                value={discipline || undefined}
                onChange={(v) => {
                  setDiscipline(v ?? '')
                  setPage(1)
                }}
                options={Object.entries(DISCIPLINE_LABEL).map(([value, label]) => ({
                  value, label,
                }))}
              />
              <Input.Search
                placeholder="搜索信息内容"
                allowClear
                style={{ width: 240 }}
                onSearch={(v) => {
                  setKeyword(v)
                  setPage(1)
                }}
              />
            </Space>
            <Table<InfoItem>
              rowKey="id"
              size="small"
              loading={loading}
              columns={columns}
              dataSource={items}
              pagination={{
                current: page,
                pageSize: PAGE_SIZE,
                total,
                showSizeChanger: false,
                showTotal: (t) => `共 ${t} 条`,
                onChange: setPage,
              }}
            />
          </Card>
        </Col>
      </Row>

      <DrawingPreviewModal
        drawingId={preview?.id ?? null}
        title={preview?.title}
        onClose={() => setPreview(null)}
      />
    </div>
  )
}

export default function ProjectInfoPage() {
  const params = useParams<{ projectId?: string }>()
  if (!params.projectId) {
    return <ProjectPicker />
  }
  return <InfoWorkspace projectId={params.projectId} />
}
