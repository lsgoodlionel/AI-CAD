import { Tabs } from 'antd'
import ProviderList from './ProviderList'
import ModelList from './ModelList'
import EngineConfigTable from './EngineConfigTable'
import CallLogsPanel from './CallLogsPanel'
import HealthDashboard from './HealthDashboard'

const TABS = [
  { key: 'health',   label: '健康状态',   children: <HealthDashboard /> },
  { key: 'provider', label: '提供商管理', children: <ProviderList /> },
  { key: 'model',    label: '模型列表',   children: <ModelList /> },
  { key: 'engine',   label: '引擎配置',   children: <EngineConfigTable /> },
  { key: 'logs',     label: '调用日志',   children: <CallLogsPanel /> },
]

export default function ModelManagement() {
  return (
    <div style={{ padding: 24 }}>
      <h2 style={{ marginBottom: 16 }}>模型路由管理</h2>
      <Tabs items={TABS} destroyInactiveTabPane />
    </div>
  )
}
