/**
 * 引擎业务参数配置页
 * - 左侧 Tab：知识图谱引擎 / 经济测算引擎
 * - 右侧：按 schema 动态渲染的参数表单（数字/滑块/选择/多选/标签输入）
 * - 每个参数单独保存，显示最后修改时间和操作人
 * - 支持一键重置为默认值
 */
import { useState, useEffect, useCallback } from 'react'
import {
  Tabs, Slider, InputNumber, Select, Button,
  Input, Space, Badge, Spin, message, Popconfirm,
} from 'antd'
import { SaveOutlined, RollbackOutlined } from '@ant-design/icons'
import { getParams, updateParam, resetParam } from '@/services/engineParams'

type ParamItem = {
  key: string; label: string; type: string
  default: unknown; value: unknown
  unit?: string; min?: number; max?: number; step?: number
  options?: string[]
  updated_at?: string; updated_by?: string
}

type ScopeKey = 'kg' | 'economic'

const SCOPES: { key: ScopeKey; label: string }[] = [
  { key: 'kg',       label: '知识图谱引擎' },
  { key: 'economic', label: '经济测算引擎' },
]

function ParamControl({
  item, onChange,
}: { item: ParamItem; onChange: (v: unknown) => void }) {
  switch (item.type) {
    case 'number':
      return (
        <InputNumber
          value={item.value as number}
          min={item.min} max={item.max} step={item.step ?? 1}
          addonAfter={item.unit}
          style={{ width: 180 }}
          onChange={onChange}
        />
      )
    case 'slider':
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Slider
            value={item.value as number}
            min={item.min ?? 0} max={item.max ?? 1} step={item.step ?? 0.01}
            style={{ width: 200 }}
            onChange={onChange}
          />
          <span style={{ minWidth: 40 }}>{(item.value as number).toFixed(2)}</span>
        </div>
      )
    case 'select':
      return (
        <Select
          value={item.value as string}
          style={{ width: 280 }}
          options={(item.options ?? []).map(o => ({ label: o, value: o }))}
          onChange={onChange}
        />
      )
    case 'multiselect':
      return (
        <Select
          mode="multiple"
          value={item.value as string[]}
          style={{ width: 360 }}
          options={(item.options ?? []).map(o => ({ label: o, value: o }))}
          onChange={onChange}
        />
      )
    case 'tags':
      return (
        <Select
          mode="tags"
          value={
            typeof item.value === 'string'
              ? (item.value as string).split(',').map(s => s.trim())
              : (item.value as string[])
          }
          style={{ width: 360 }}
          tokenSeparators={[',']}
          onChange={v => onChange(v.join(','))}
        />
      )
    default:
      return (
        <Input
          value={item.value as string}
          style={{ width: 280 }}
          onChange={e => onChange(e.target.value)}
        />
      )
  }
}

function ParamSection({ scope }: { scope: ScopeKey }) {
  const [params, setParams] = useState<ParamItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    const data = await getParams(scope)
    setParams(data)
    setLoading(false)
  }, [scope])

  useEffect(() => { load() }, [load])

  const handleChange = (key: string, value: unknown) => {
    setParams(prev => prev.map(p => p.key === key ? { ...p, value } : p))
  }

  const handleSave = async (item: ParamItem) => {
    setSaving(item.key)
    await updateParam(scope, item.key, item.value)
    message.success(`已保存：${item.label}`)
    await load()
    setSaving(null)
  }

  const handleReset = async (item: ParamItem) => {
    await resetParam(scope, item.key)
    message.info(`已重置为默认值：${item.label}`)
    await load()
  }

  const isModified = (item: ParamItem) =>
    JSON.stringify(item.value) !== JSON.stringify(item.default)

  return (
    <Spin spinning={loading}>
      <div style={{ maxWidth: 800 }}>
        {params.map(item => (
          <div
            key={item.key}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 16,
              padding: '12px 0', borderBottom: '1px solid #f0f0f0',
            }}
          >
            {/* 左：参数标签 */}
            <div style={{ width: 220, flexShrink: 0 }}>
              <Space>
                {isModified(item) && <Badge dot />}
                <span style={{ fontWeight: isModified(item) ? 600 : 400 }}>
                  {item.label}
                </span>
              </Space>
              <div style={{ color: '#999', fontSize: 11, marginTop: 2 }}>
                {item.key}
              </div>
              {item.updated_at && (
                <div style={{ color: '#bbb', fontSize: 11 }}>
                  {new Date(item.updated_at).toLocaleString('zh-CN')}
                </div>
              )}
            </div>

            {/* 中：控件 */}
            <div style={{ flex: 1 }}>
              <ParamControl item={item} onChange={v => handleChange(item.key, v)} />
              <div style={{ color: '#aaa', fontSize: 11, marginTop: 4 }}>
                默认值：{JSON.stringify(item.default)}
              </div>
            </div>

            {/* 右：操作 */}
            <Space>
              <Button
                icon={<SaveOutlined />} size="small" type="primary"
                loading={saving === item.key}
                disabled={!isModified(item)}
                onClick={() => handleSave(item)}
              >
                保存
              </Button>
              <Popconfirm
                title={`重置"${item.label}"为默认值？`}
                onConfirm={() => handleReset(item)}
                disabled={!item.updated_at}
              >
                <Button
                  icon={<RollbackOutlined />} size="small"
                  disabled={!item.updated_at}
                >
                  重置
                </Button>
              </Popconfirm>
            </Space>
          </div>
        ))}
      </div>
    </Spin>
  )
}

export default function EngineParams() {
  return (
    <div style={{ padding: 24 }}>
      <h2 style={{ marginBottom: 4 }}>引擎业务参数</h2>
      <p style={{ color: '#888', marginBottom: 16 }}>
        所有参数变更即时生效（引擎下次调用时读取），无需重启服务。
        修改后标有蓝点，保存后蓝点消失。
      </p>
      <Tabs
        items={SCOPES.map(s => ({
          key: s.key,
          label: s.label,
          children: <ParamSection scope={s.key} />,
        }))}
        destroyInactiveTabPane
      />
    </div>
  )
}
