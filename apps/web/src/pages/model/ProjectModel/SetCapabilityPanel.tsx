import { Alert, Descriptions, Space, Table, Tag, Typography } from 'antd'
import type { CapabilityLevel, SetCapabilityPayload } from '@/services/projectModel'

const { Text } = Typography

/**
 * 建模能力与降级说明。
 *
 * **为什么要有这块**：模型 13 层里 10 层标高是 `4.5m` 默认值硬推的，
 * 而界面上完全看不出来——用户看到的 `F6 24.9` 与从图纸读出的 `36.800`
 * 长得一模一样。降级如果不可见，用户就会把默认值当成图纸实测值。
 *
 * 这里把 `partial_set.assess_capability` 的结论原样摆出来，不做美化。
 */

const LEVEL_META: Record<CapabilityLevel, { color: string; label: string }> = {
  full: { color: 'green', label: '图纸实测' },
  partial: { color: 'orange', label: '降级推断' },
  none: { color: 'red', label: '缺图纸依据' },
}

const ROLE_LABELS: Record<string, string> = {
  coordinate_base: 'P0 坐标基准图',
  floor_skeleton: 'P1 楼层骨架图',
  elevation_reference: 'P2 标高来源图',
  component_source: 'P3 构件来源图',
  detail: 'P5 详图',
  non_geometric: 'P6 非几何（目录/说明/系统图）',
  unknown: '未判别',
}

function Level({ value }: { value: CapabilityLevel }) {
  const meta = LEVEL_META[value] ?? LEVEL_META.none
  return <Tag color={meta.color}>{meta.label}</Tag>
}

export default function SetCapabilityPanel({
  payload,
}: {
  payload?: SetCapabilityPayload | null
}) {
  if (!payload) {
    // 旧模型没有这个字段——如实说不可用，不要显示一个假的「正常」
    return <Text type="secondary">该模型构建于本功能上线前，无能力评估数据。</Text>
  }

  const { capability, roles, stages, learned_patterns: patterns } = payload
  const ua = payload.unit_assignment

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      {!capability.can_build && (
        <Alert
          type="error"
          showIcon
          message="这批图纸建不出模型"
          description="没有任何可产出构件的平面图。"
        />
      )}

      <Descriptions size="small" column={3} bordered>
        <Descriptions.Item label="世界坐标">
          <Level value={capability.world_coords} />
        </Descriptions.Item>
        <Descriptions.Item label="楼层">
          <Level value={capability.floors} />
        </Descriptions.Item>
        <Descriptions.Item label="标高">
          <Level value={capability.elevations} />
        </Descriptions.Item>
      </Descriptions>

      {capability.degradations.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`本模型有 ${capability.degradations.length} 处降级，结果的可用范围受限`}
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {capability.degradations.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          }
        />
      )}

      {ua && (
        <div>
          <Text strong>单体归属</Text>
          <div style={{ marginTop: 4, marginBottom: 4 }}>
            <Text type="secondary">
              {/*
                这里刻意把「本就没有单体」单列。原先只报一个「未分配 80.8%」,
                而其中一半以上是目录/说明/详图/围护图——本来就不属于任何单体。
              */}
              目录/说明/详图/围护图本就不属于任何单体,不计入待处理。
            </Text>
          </div>
          <Space wrap>
            <Tag color="green">{`已识别单体 ${ua.assigned}`}</Tag>
            <Tag color="orange">{`降级挂默认单体 ${ua.defaulted}`}</Tag>
            <Tag>{`本就无单体归属 ${ua.not_applicable}`}</Tag>
            <Tag color={ua.unresolved > 0 ? 'red' : undefined}>
              {`无法判定 ${ua.unresolved}`}
            </Tag>
            <Tag color="blue">{`需处理 ${ua.needs_attention}`}</Tag>
          </Space>
        </div>
      )}

      <div>
        <Text strong>图纸角色分布</Text>
        <Table
          rowKey="role"
          size="small"
          pagination={false}
          style={{ marginTop: 8 }}
          dataSource={Object.entries(roles).map(([role, count]) => ({ role, count }))}
          columns={[
            {
              title: '角色',
              dataIndex: 'role',
              render: (role: string) => ROLE_LABELS[role] ?? role,
            },
            { title: '张数', dataIndex: 'count', width: 90 },
          ]}
        />
      </div>

      {stages.length > 0 && (
        <div>
          <Text strong>处理顺序（按依赖）</Text>
          <div style={{ marginTop: 8 }}>
            {stages.map((s, i) => (
              <span key={s.role}>
                {i > 0 && <Text type="secondary"> → </Text>}
                <Tag>{`${ROLE_LABELS[s.role] ?? s.role} ×${s.count}`}</Tag>
              </span>
            ))}
          </div>
        </div>
      )}

      {Object.keys(patterns).length > 0 && (
        <div>
          <Text strong>{`从本批图纸学到的编号规律（${Object.keys(patterns).length} 段）`}</Text>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary">
              这些规律是从本项目图纸归纳出来的，不是内置的；换一套图号体系会重新学。
            </Text>
          </div>
        </div>
      )}
    </Space>
  )
}
