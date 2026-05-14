/**
 * 新增引擎配置弹窗
 * 字段：引擎名称（13个预定义）× 任务类型 × 关联模型 × 推理参数
 */
import { useEffect, useState } from 'react'
import {
  Modal, Form, Select, Slider, InputNumber, Row, Col, message, Tooltip,
} from 'antd'
import {
  listModels, listEngineNames, listEngineConfigs, createEngineConfig,
} from '@/services/modelManagement'

type Model = {
  id: string
  display_name: string
  model_id: string
  provider_name: string
  provider_type: string
  supports_vision: boolean
  is_active: boolean
}

type EngineConfig = {
  engine_name: string
  task_type: string
}

type Props = {
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

const TASK_TYPES = [
  { value: 'primary',    label: '主模型 (primary)' },
  { value: 'fallback_1', label: '备用1 (fallback_1)' },
  { value: 'fallback_2', label: '备用2 (fallback_2)' },
  { value: 'batch',      label: '批量 (batch)' },
]

type EngineRecommendation = {
  title: string
  description: string
  recommendedModelIds: string[]
  config: {
    temperature: number
    max_tokens: number
    top_p: number
    frequency_penalty: number
  }
  reason: string
}

const DEFAULT_RECOMMENDATION: EngineRecommendation = {
  title: '通用推理引擎',
  description: '用于未细分的文本理解、结构化处理和轻量推理任务。',
  recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
  config: { temperature: 0.1, max_tokens: 2048, top_p: 1.0, frequency_penalty: 0.0 },
  reason: '默认优先选择低温度和中等输出长度，保证结果稳定，同时控制成本和延迟。',
}

const ENGINE_RECOMMENDATIONS: Record<string, EngineRecommendation> = {
  regulation_classifier: {
    title: '条文分类引擎',
    description: '识别规范段落属于简单条文、条件条文、交叉引用、定义或无关内容，并判断是否强制性条文。',
    recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
    config: { temperature: 0, max_tokens: 1024, top_p: 1.0, frequency_penalty: 0.0 },
    reason: '分类任务需要低随机性和稳定 JSON 输出，Haiku 类轻量模型速度快、成本低，足够覆盖大批量段落预筛。',
  },
  regulation_extractor: {
    title: '条文实体提取引擎',
    description: '从规范条文中抽取条文编号、标题、义务等级、适用条件、关键参数和原文结构。',
    recommendedModelIds: ['claude-sonnet-4-6', 'gpt-4o', 'deepseek-chat'],
    config: { temperature: 0.1, max_tokens: 4096, top_p: 1.0, frequency_penalty: 0.0 },
    reason: '实体提取比分类更依赖上下文理解和结构化稳定性，Sonnet/GPT-4o 在复杂条文解析上更稳。',
  },
  kg_compliance_reasoning: {
    title: '合规推理引擎',
    description: '结合知识图谱、规范条文和图纸/业务事实，判断是否满足规范要求并给出证据链。',
    recommendedModelIds: ['claude-sonnet-4-6', 'deepseek-reasoner', 'gpt-4o'],
    config: { temperature: 0.1, max_tokens: 6144, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '合规判断需要更强推理能力和较长输出，低温度减少结论漂移，较大 max_tokens 保留证据链。',
  },
  kg_suggestion_generator: {
    title: '修改建议生成引擎',
    description: '基于审查结果生成面向设计人员的修改建议、风险提示和落地整改方向。',
    recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
    config: { temperature: 0.2, max_tokens: 2048, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '建议生成需要一定表达弹性，但仍要保持工程措辞稳定，轻量模型可降低交互延迟。',
  },
  kg_diff_analyzer: {
    title: '规范版本对比引擎',
    description: '对比不同版本规范条文差异，识别新增、删除、修改内容和潜在影响。',
    recommendedModelIds: ['claude-sonnet-4-6', 'gpt-4o', 'deepseek-reasoner'],
    config: { temperature: 0.05, max_tokens: 6144, top_p: 1.0, frequency_penalty: 0.0 },
    reason: '版本对比要求严谨、少遗漏，推荐上下文和推理能力更强的模型，并使用低温度控制稳定性。',
  },
  rag_qa: {
    title: '规范问答引擎',
    description: '基于检索到的规范条文回答业务问题，并引用相关依据。',
    recommendedModelIds: ['claude-sonnet-4-6', 'gpt-4o', 'deepseek-chat'],
    config: { temperature: 0.1, max_tokens: 4096, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '规范问答需要兼顾检索依据和自然语言解释，Sonnet/GPT-4o 对长上下文综合更稳。',
  },
  rag_rewriter: {
    title: '查询改写引擎',
    description: '将用户问题改写为更适合规范检索的关键词、同义表达和结构化查询。',
    recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
    config: { temperature: 0.2, max_tokens: 1024, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '查询改写属于短文本轻量任务，推荐低成本模型，在保持召回质量的同时减少等待时间。',
  },
  rebar_annotation_parser: {
    title: '钢筋标注解析引擎',
    description: '解析钢筋标注、规格、间距、锚固和构造信息，供经济测算和审查使用。',
    recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
    config: { temperature: 0, max_tokens: 2048, top_p: 1.0, frequency_penalty: 0.0 },
    reason: '标注解析要求格式稳定、少发散，低温度轻量模型更适合高频结构化抽取。',
  },
  cost_explanation_writer: {
    title: '经济说明生成引擎',
    description: '根据经济测算结果生成成本变化、计算依据和商务说明。',
    recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
    config: { temperature: 0.2, max_tokens: 2048, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '经济说明需要清晰表达但推理深度有限，轻量模型可以兼顾质量、速度和成本。',
  },
  optimization_hint_writer: {
    title: '优化建议生成引擎',
    description: '基于图纸审查和经济测算结果生成可执行的设计优化建议。',
    recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
    config: { temperature: 0.25, max_tokens: 2048, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '优化建议允许适度发散，但仍需工程可落地，推荐略高温度和轻量模型。',
  },
  report_summary_writer: {
    title: '审查摘要引擎',
    description: '汇总技术审查、经济审查和风险点，生成管理层可读的审查摘要。',
    recommendedModelIds: ['claude-sonnet-4-6', 'gpt-4o', 'deepseek-chat'],
    config: { temperature: 0.15, max_tokens: 4096, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '摘要需要跨模块整合信息，推荐更强的综合能力模型，低温度保证结论一致。',
  },
  drawing_visual_analyzer: {
    title: '图纸视觉理解引擎',
    description: '理解复杂图纸截图、构件关系和视觉标注，用于辅助图纸审查。',
    recommendedModelIds: ['claude-sonnet-4-6', 'gpt-4o'],
    config: { temperature: 0.1, max_tokens: 4096, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '视觉理解需要多模态能力，优先推荐支持 vision 的强模型；不可用时再回退本地可用模型。',
  },
  incentive_description_writer: {
    title: '创效提案描述引擎',
    description: '将创效提案要点整理为背景、措施、收益、风险和推广价值说明。',
    recommendedModelIds: ['claude-haiku-4-5-20251001', 'gpt-4o-mini', 'deepseek-chat'],
    config: { temperature: 0.25, max_tokens: 2048, top_p: 0.9, frequency_penalty: 0.0 },
    reason: '提案描述以表达组织为主，轻量模型足够稳定，略高温度可提升文本可读性。',
  },
}

export default function AddEngineConfigModal({ open, onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const [models, setModels] = useState<Model[]>([])
  const [engineNames, setEngineNames] = useState<string[]>([])
  const [existingConfigs, setExistingConfigs] = useState<EngineConfig[]>([])
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) return
    Promise.all([listModels(), listEngineNames(), listEngineConfigs()]).then(([ms, names, configs]) => {
      setModels(ms)
      setEngineNames(names)
      setExistingConfigs(configs)
    })
  }, [open])

  const findRecommendedModel = (engineName: string) => {
    const recommendation = ENGINE_RECOMMENDATIONS[engineName] ?? DEFAULT_RECOMMENDATION
    const activeModels = models.filter(m => m.is_active)
    const recommended = recommendation.recommendedModelIds
      .map(modelId => activeModels.find(m => m.model_id === modelId))
      .find(Boolean)
    if (recommended) return recommended.id

    const localModel = activeModels.find(m => m.provider_type === 'ollama')
    return localModel?.id
  }

  const handleEngineChange = (engineName: string) => {
    const recommendation = ENGINE_RECOMMENDATIONS[engineName] ?? DEFAULT_RECOMMENDATION
    form.setFieldsValue({
      engine_name: engineName,
      model_id: findRecommendedModel(engineName),
      ...recommendation.config,
    })
  }

  const handleOk = async () => {
    const values = await form.validateFields()
    const exists = existingConfigs.some(
      item => item.engine_name === values.engine_name && item.task_type === values.task_type,
    )
    if (exists) {
      message.warning('该引擎的任务类型配置已存在，请编辑现有配置或选择其他任务类型')
      return
    }

    setSubmitting(true)
    try {
      await createEngineConfig(values)
      message.success('引擎配置已创建，路由器 30s 内生效')
      form.resetFields()
      onSuccess()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '引擎配置创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  const modelOptions = models.map(m => ({
    value: m.id,
    label: `${m.display_name} [${m.provider_name}]`,
    disabled: !m.is_active,
  }))

  const engineOptions = engineNames.map(n => {
    const recommendation = ENGINE_RECOMMENDATIONS[n] ?? DEFAULT_RECOMMENDATION
    return {
      value: n,
      searchText: n,
      label: (
        <Tooltip
          placement="right"
          title={(
            <div style={{ maxWidth: 360 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>{recommendation.title}</div>
              <div>作用：{recommendation.description}</div>
              <div style={{ marginTop: 6 }}>默认推荐模型：{recommendation.recommendedModelIds.join(' / ')}</div>
              <div style={{ marginTop: 6 }}>
                默认推荐配置：temperature={recommendation.config.temperature}，
                max_tokens={recommendation.config.max_tokens}，
                top_p={recommendation.config.top_p}，
                frequency_penalty={recommendation.config.frequency_penalty}
              </div>
              <div style={{ marginTop: 6 }}>推荐原因：{recommendation.reason}</div>
            </div>
          )}
        >
          <span>{n}</span>
        </Tooltip>
      ),
    }
  })

  return (
    <Modal
      title="添加引擎配置"
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      okText="创建"
      confirmLoading={submitting}
      width={620}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ task_type: 'primary', temperature: 0.1, max_tokens: 2048, top_p: 1.0, frequency_penalty: 0.0 }}
        style={{ marginTop: 16 }}
      >
        <Row gutter={16}>
          <Col span={14}>
            <Form.Item name="engine_name" label="引擎名称" rules={[{ required: true }]}>
              <Select
                showSearch
                placeholder="选择引擎"
                optionLabelProp="searchText"
                options={engineOptions}
                onChange={handleEngineChange}
                filterOption={(input, opt) =>
                  ((opt?.searchText as string) ?? '').toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>
          </Col>
          <Col span={10}>
            <Form.Item name="task_type" label="任务类型" rules={[{ required: true }]}>
              <Select options={TASK_TYPES} />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item name="model_id" label="关联模型" rules={[{ required: true }]}>
          <Select
            showSearch
            placeholder="选择模型"
            options={modelOptions}
            filterOption={(input, opt) =>
              (opt?.label as string).toLowerCase().includes(input.toLowerCase())
            }
          />
        </Form.Item>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="temperature" label={`温度: ${form.getFieldValue('temperature') ?? 0.1}`}>
              <Slider min={0} max={2} step={0.05}
                onChange={() => form.setFieldValue('temperature', form.getFieldValue('temperature'))}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="top_p" label={`Top-P: ${form.getFieldValue('top_p') ?? 1.0}`}>
              <Slider min={0} max={1} step={0.05}
                onChange={() => form.setFieldValue('top_p', form.getFieldValue('top_p'))}
              />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="max_tokens" label="Max Tokens">
              <InputNumber min={1} max={32000} step={256} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="frequency_penalty" label="Frequency Penalty">
              <InputNumber min={-2} max={2} step={0.1} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>
      </Form>
    </Modal>
  )
}
