/**
 * 管线待办建议面板（Phase D D-15：数据看板接入 D-08 事件编排层）
 *
 * 展示当前项目「卡在哪一步」——事件编排层（core/pipeline/）在关键节点自动生成的
 * 建议待办（如「AI 审图完成，建议重建模型」「模型算量完成，建议创建创效提案」）。
 * 遵循「自动打底、人工确认」原则：本面板只读展示 + 快捷跳转/忽略，绝不代为触发
 * 重建模型、创建提案等硬动作（跳转后仍需用户在对应页面手动发起）。
 */
import { useEffect, useState } from 'react'
import { useNavigate, request } from '@umijs/max'
import { Card, List, Tag, Button, Space, Typography, Spin, Empty } from 'antd'
import { ArrowRightOutlined, CloseOutlined } from '@ant-design/icons'
import HelpTip from '@/components/HelpTip'

const { Text } = Typography

interface PipelineSuggestion {
  id: string
  project_id: string
  suggestion_type: string
  status: string
  title: string
  summary: string | null
  created_at: string
}

interface SuggestionsEnvelope {
  success: boolean
  data: { items: PipelineSuggestion[]; total: number }
  error: string | null
}

const SUGGESTION_TYPE_MAP: Record<string, { label: string; color: string; path: (id: string) => string }> = {
  rebuild_model: {
    label: '建议重建模型',
    color: 'blue',
    path: (id) => `/model/${id}`,
  },
  create_proposal: {
    label: '建议创建创效提案',
    color: 'gold',
    path: (id) => `/projects/${id}/quantities`,
  },
}

function describeType(suggestionType: string): { label: string; color: string; path: (id: string) => string } {
  return SUGGESTION_TYPE_MAP[suggestionType] ?? {
    label: suggestionType,
    color: 'default',
    path: (id) => `/projects/${id}/hub`,
  }
}

interface PipelineStatusPanelProps {
  projectId: string
}

export default function PipelineStatusPanel({ projectId }: PipelineStatusPanelProps) {
  const navigate = useNavigate()
  const [items, setItems] = useState<PipelineSuggestion[]>([])
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  useEffect(() => {
    if (!projectId) {
      setItems([])
      return
    }
    let cancelled = false
    setLoading(true)
    request<SuggestionsEnvelope>(`/api/v1/projects/${projectId}/pipeline/suggestions`)
      .then((res: SuggestionsEnvelope) => {
        if (cancelled) return
        setItems(res?.data?.items ?? [])
      })
      .catch(() => {
        if (!cancelled) setItems([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  const resolveSuggestion = async (suggestionId: string, action: 'accept' | 'dismiss') => {
    setBusyId(suggestionId)
    try {
      await request(`/api/v1/projects/${projectId}/pipeline/suggestions/${suggestionId}/${action}`, {
        method: 'POST',
      })
      setItems((prev) => prev.filter((item) => item.id !== suggestionId))
    } catch {
      // 静默失败已由全局 errorConfig 提示，此处仅避免卡住 busy 状态
    } finally {
      setBusyId(null)
    }
  }

  if (!projectId) return null
  if (!loading && items.length === 0) return null

  return (
    <Card
      size="small"
      style={{ marginBottom: 16 }}
      title={
        <Space>
          <span>管线待办建议</span>
          <HelpTip
            title="管线待办建议"
            content="系统在关键节点（如 AI 审图完成、模型算量完成）自动生成的下一步建议，仅提示不代为执行，需人工确认后跳转处理。"
            anchor=""
          />
        </Space>
      }
    >
      {loading ? (
        <Spin size="small" />
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待办建议" />
      ) : (
        <List
          size="small"
          dataSource={items}
          renderItem={(item) => {
            const meta = describeType(item.suggestion_type)
            return (
              <List.Item
                actions={[
                  <Button
                    key="go"
                    type="link"
                    size="small"
                    icon={<ArrowRightOutlined />}
                    loading={busyId === item.id}
                    onClick={() => {
                      resolveSuggestion(item.id, 'accept')
                      navigate(meta.path(projectId))
                    }}
                  >
                    去处理
                  </Button>,
                  <Button
                    key="dismiss"
                    type="link"
                    size="small"
                    icon={<CloseOutlined />}
                    loading={busyId === item.id}
                    onClick={() => resolveSuggestion(item.id, 'dismiss')}
                  >
                    忽略
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <Tag color={meta.color}>{meta.label}</Tag>
                      <Text>{item.title}</Text>
                    </Space>
                  }
                  description={item.summary ?? undefined}
                />
              </List.Item>
            )
          }}
        />
      )}
    </Card>
  )
}
