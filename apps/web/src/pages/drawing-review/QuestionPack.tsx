import { Card, Typography, Space, message } from 'antd'
import { CopyOutlined } from '@ant-design/icons'
import type { QuestionPack as QuestionPackData } from '@/services/drawingReview'

const { Paragraph, Text } = Typography

interface QuestionPackProps {
  pack: QuestionPackData
}

const copy = async (text: string) => {
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制')
  } catch {
    message.warning('复制失败，请手动选择文本')
  }
}

/** 问题包：主问题（可复制）/ 补充问题 / 证据缺口 三段展示（契约 V2-3） */
export default function QuestionPack({ pack }: QuestionPackProps) {
  const hasContent = pack.主问题 || pack.补充问题 || pack.证据缺口
  if (!hasContent) return null

  return (
    <Card size="small" title="问题包（主问题 + 补充问题 + 证据缺口）">
      <Space direction="vertical" size="small" style={{ width: '100%' }}>
        {pack.主问题 && (
          <div>
            <Text type="secondary" strong>
              主问题
            </Text>
            <Paragraph
              strong
              style={{ margin: '4px 0 0' }}
              copyable={{
                text: pack.主问题,
                icon: <CopyOutlined />,
                tooltips: ['复制主问题', '已复制'],
                onCopy: () => copy(pack.主问题),
              }}
            >
              {pack.主问题}
            </Paragraph>
          </div>
        )}
        {pack.补充问题 && (
          <div>
            <Text type="secondary" strong>
              补充问题
            </Text>
            <Paragraph style={{ margin: '4px 0 0' }}>{pack.补充问题}</Paragraph>
          </div>
        )}
        {pack.证据缺口 && (
          <div>
            <Text type="secondary" strong>
              证据缺口
            </Text>
            <Paragraph type="warning" style={{ margin: '4px 0 0' }}>
              {pack.证据缺口}
            </Paragraph>
          </div>
        )}
      </Space>
    </Card>
  )
}
