import { useState } from 'react'
import { Card, Tabs, List, Tag, Typography, Button, Empty, Space, message } from 'antd'
import { FileTextOutlined } from '@ant-design/icons'
import {
  genDocument,
  type DocumentClause,
  type DocumentOutput as DocumentOutputData,
  type DocKind,
} from '@/services/drawingReview'

const { Paragraph } = Typography

interface DocumentOutputProps {
  /** auditText 已返回的文书输出（优先直接渲染，避免多余请求） */
  output?: DocumentOutputData
  /** 用于「按当前文本生成」按钮重新调用 genDocument 的入参 */
  source?: { title: string; body: string; discipline?: string }
}

const copy = async (text: string) => {
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制')
  } catch {
    message.warning('复制失败，请手动选择文本')
  }
}

interface ClauseListProps {
  clauses: DocumentClause[]
}

function ClauseList({ clauses }: ClauseListProps) {
  if (!clauses.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无文书条目" />
  }
  return (
    <List
      dataSource={clauses}
      renderItem={(clause, i) => (
        <List.Item
          key={`${clause.type}-${i}`}
          actions={[
            <a key="copy" onClick={() => copy(clause.text)}>
              复制
            </a>,
          ]}
        >
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            {clause.type && <Tag color="cyan">{clause.type}</Tag>}
            <Paragraph style={{ margin: 0 }}>{clause.text}</Paragraph>
          </Space>
        </List.Item>
      )}
    />
  )
}

/**
 * 文书化输出区：两 Tab（会审纪要口径 / 设计答复口径）。
 * 默认直接渲染 auditText 返回的 文书输出；点击「按当前文本生成」时
 * 调用 POST /document 重新生成对应口径，覆盖本地展示（不改动 auditText 结果）。
 */
export default function DocumentOutput({ output, source }: DocumentOutputProps) {
  const [minutes, setMinutes] = useState<DocumentClause[]>(output?.会审纪要口径 ?? [])
  const [reply, setReply] = useState<DocumentClause[]>(output?.设计答复口径 ?? [])
  const [genKind, setGenKind] = useState<DocKind | null>(null)

  const handleGenerate = async (kind: DocKind) => {
    if (!source?.title || !source?.body) {
      message.warning('请先填写标题与正文')
      return
    }
    setGenKind(kind)
    try {
      const clauses = await genDocument({
        title: source.title,
        body: source.body,
        discipline: source.discipline,
        doc_kind: kind,
      })
      if (kind === 'minutes') setMinutes(clauses)
      else setReply(clauses)
      message.success('文书已生成')
    } catch {
      message.error('文书生成失败，请稍后重试')
    } finally {
      setGenKind(null)
    }
  }

  const genButton = (kind: DocKind, label: string) =>
    source ? (
      <Button
        size="small"
        icon={<FileTextOutlined />}
        loading={genKind === kind}
        onClick={() => handleGenerate(kind)}
      >
        {label}
      </Button>
    ) : null

  return (
    <Card size="small" title="文书化输出">
      <Tabs
        items={[
          {
            key: 'minutes',
            label: '会审纪要口径',
            children: (
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                {genButton('minutes', '按当前文本生成纪要')}
                <ClauseList clauses={minutes} />
              </Space>
            ),
          },
          {
            key: 'reply',
            label: '设计答复口径',
            children: (
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                {genButton('reply', '按当前文本生成答复')}
                <ClauseList clauses={reply} />
              </Space>
            ),
          },
        ]}
      />
    </Card>
  )
}
