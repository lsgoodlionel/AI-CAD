/**
 * 规范搜索（所有角色可用，此处放在管理后台供验证）
 */
import { useState } from 'react'
import { Input, Select, Spin, List, Tag, Typography, Space, Empty } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { searchRegulations } from '@/services/regulations'

const { Text, Paragraph } = Typography

const OBL_COLOR: Record<string, string> = {
  MUST: 'red', MUST_NOT: 'volcano', SHOULD: 'blue', MAY: 'cyan',
}
const OBL_LABEL: Record<string, string> = {
  MUST: '必须', MUST_NOT: '严禁', SHOULD: '应', MAY: '宜',
}
const DISCIPLINE_OPTIONS = [
  { label: '全部专业', value: '' },
  { label: '通用',     value: 'general' },
  { label: '结构',     value: 'structure' },
  { label: '建筑',     value: 'architecture' },
  { label: '机电',     value: 'mep' },
  { label: '消防',     value: 'fire' },
  { label: '装修',     value: 'decoration' },
]

type Result = {
  id: string
  article_no: string
  title: string | null
  content_preview: string
  obligation_level: string
  is_mandatory: boolean
  book_title: string
  std_no: string | null
}

export default function RegulationSearch() {
  const [query, setQuery] = useState('')
  const [discipline, setDiscipline] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  const handleSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setSearched(true)
    try {
      const res = await searchRegulations({ q: query, discipline: discipline || undefined, limit: 30 })
      setResults(res.items ?? [])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: 800 }}>
      <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
        <Select
          style={{ width: 120 }}
          options={DISCIPLINE_OPTIONS}
          value={discipline}
          onChange={setDiscipline}
        />
        <Input
          placeholder="输入关键词搜索规范条文..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onPressEnter={handleSearch}
          suffix={<SearchOutlined style={{ cursor: 'pointer' }} onClick={handleSearch} />}
          allowClear
        />
      </Space.Compact>

      <Spin spinning={loading}>
        {searched && results.length === 0 && !loading && (
          <Empty description="未找到相关条文" />
        )}
        <List
          dataSource={results}
          renderItem={item => (
            <List.Item key={item.id} style={{ alignItems: 'flex-start' }}>
              <div style={{ width: '100%' }}>
                <Space wrap style={{ marginBottom: 4 }}>
                  {item.is_mandatory && <Tag color="red">强条</Tag>}
                  <Tag color={OBL_COLOR[item.obligation_level] ?? 'default'}>
                    {OBL_LABEL[item.obligation_level] ?? item.obligation_level}
                  </Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {item.std_no ?? item.book_title} · {item.article_no}
                  </Text>
                </Space>
                {item.title && <Text strong style={{ display: 'block', marginBottom: 4 }}>{item.title}</Text>}
                <Paragraph ellipsis={{ rows: 3, expandable: true }} style={{ marginBottom: 0 }}>
                  {item.content_preview}
                </Paragraph>
              </div>
            </List.Item>
          )}
          locale={{ emptyText: null }}
        />
      </Spin>
    </div>
  )
}
