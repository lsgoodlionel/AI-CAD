import type { ReactNode } from 'react'
import {
  ApartmentOutlined,
  AppstoreOutlined,
  BankOutlined,
  ClearOutlined,
  DeploymentUnitOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons'
import { Button, Empty, Space, Tag, Tooltip, Tree, Typography } from 'antd'
import type { DataNode } from 'antd/es/tree'
import type { SemanticNodeType } from '@/services/projectModel'
import type { SemanticTreeGroup, SemanticTreeNodeView } from './types'

const { Text } = Typography

interface SemanticTreePanelProps {
  groups: SemanticTreeGroup[]
  selectedNodeId?: string | null
  onSelectNode: (node: SemanticTreeNodeView | null) => void
}

const TYPE_ICON: Record<SemanticNodeType, ReactNode> = {
  building_unit: <BankOutlined />,
  sub_zone: <ApartmentOutlined />,
  functional_space: <AppstoreOutlined />,
  construction_zone: <DeploymentUnitOutlined />,
}

const STATUS_META = {
  candidate: { label: '候选', color: 'gold' },
  confirmed: { label: '已确认', color: 'green' },
  rejected: { label: '已拒绝', color: 'default' },
  merged: { label: '已合并', color: 'blue' },
} as const

const SOURCE_LABEL = {
  automatic: '自动',
  manual: '人工',
  legacy_inference: '兼容',
} as const

function renderTitle(node: SemanticTreeNodeView) {
  return (
    <Space size={6} wrap>
      <Text>{TYPE_ICON[node.nodeType]}</Text>
      <Text strong>{node.canonicalName}</Text>
      <Tag color={STATUS_META[node.status].color}>{STATUS_META[node.status].label}</Tag>
      <Tag>{SOURCE_LABEL[node.source]}</Tag>
      {node.parentName ? <Text type="secondary">父级 {node.parentName}</Text> : null}
      {node.confidence > 0 ? (
        <Text type="secondary">{Math.round(node.confidence * 100)}%</Text>
      ) : null}
    </Space>
  )
}

export default function SemanticTreePanel({
  groups,
  selectedNodeId,
  onSelectNode,
}: SemanticTreePanelProps) {
  if (groups.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无语义树" />
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text strong>按语义类型分组</Text>
        <Space size={4}>
          <Tooltip title="语义树将单体、分区、功能空间与施工分区分开显示，避免误归类。">
            <Button size="small" type="text" icon={<InfoCircleOutlined />} />
          </Tooltip>
          <Tooltip title="清除当前语义选择">
            <Button
              size="small"
              type="text"
              icon={<ClearOutlined />}
              disabled={!selectedNodeId}
              onClick={() => onSelectNode(null)}
            />
          </Tooltip>
        </Space>
      </Space>

      {groups.map((group) => {
        const treeData: DataNode[] = group.nodes.map((node) => ({
          key: node.id,
          title: renderTitle(node),
          isLeaf: true,
        }))
        return (
          <div key={group.type} data-testid={`semantic-group-${group.type}`}>
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <Space>
                <Text strong>{group.label}</Text>
                <Tag>{group.nodes.length}</Tag>
              </Space>
              <Tree
                blockNode
                selectedKeys={selectedNodeId ? [selectedNodeId] : []}
                treeData={treeData}
                onSelect={(keys) => {
                  const nextId = typeof keys[0] === 'string' ? keys[0] : null
                  const nextNode = nextId
                    ? group.nodes.find((node) => node.id === nextId) ?? null
                    : null
                  onSelectNode(nextNode)
                }}
              />
            </Space>
          </div>
        )
      })}
    </Space>
  )
}
