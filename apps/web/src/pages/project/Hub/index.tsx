/**
 * 项目工作台（Project Hub） — Phase D 泳道1 D-01 + D-03
 * 单个项目一屏呈现全流程流水线：上传→审查→建模→算量→创效
 * 无 :id 参数时展示项目选择器；有 :id 时展示工作台主体。
 */
import { useEffect } from 'react'
import { useParams } from '@umijs/max'
import { Alert, Spin } from 'antd'
import ProjectPicker from './ProjectPicker'
import HubHeader from './HubHeader'
import PipelineSteps from './PipelineSteps'
import PipelineCards from './PipelineCards'
import RecentActivityPanel from './RecentActivityPanel'
import { useProjectHubData } from './useProjectHubData'
import { inferHubStep } from './inferHubStep'
import { LAST_PROJECT_STORAGE_KEY } from './constants'

export default function ProjectHub() {
  const params = useParams<{ id?: string }>()
  const projectId = params.id ?? ''

  if (!projectId) {
    return <ProjectPicker />
  }

  return <ProjectHubBody projectId={projectId} />
}

interface ProjectHubBodyProps {
  projectId: string
}

function ProjectHubBody({ projectId }: ProjectHubBodyProps) {
  const { data, loading, error } = useProjectHubData(projectId)

  useEffect(() => {
    window.localStorage.setItem(LAST_PROJECT_STORAGE_KEY, projectId)
  }, [projectId])

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Spin style={{ marginTop: 80 }} />
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="error" showIcon message={error} />
      </div>
    )
  }

  if (!data) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="warning" showIcon message="暂无数据，请先创建图纸或提案" />
      </div>
    )
  }

  const currentStep = inferHubStep(data)

  return (
    <div style={{ padding: 24 }}>
      <HubHeader projectId={projectId} projectName={data.project?.name ?? ''} />
      <PipelineSteps projectId={projectId} currentStep={currentStep} />
      <PipelineCards projectId={projectId} data={data} />
      <RecentActivityPanel activities={data.recent_activity} />
    </div>
  )
}
