/**
 * 顶部常驻流程 Steps：上传图纸 → AI审查 → 工程建模 → 算量 → 创效提案
 * 每一步显示「去完成 →」按钮，直达对应模块并带上项目上下文。
 */
import { useNavigate } from '@umijs/max'
import { Button, Card, Steps } from 'antd'
import { HUB_STEP_TITLES } from './constants'
import type { HubStepIndex } from './types'

interface PipelineStepsProps {
  projectId: string
  currentStep: HubStepIndex
}

const STEP_TARGET_PATH: readonly ((projectId: string) => string)[] = [
  () => '/drawings',
  () => '/drawings',
  (projectId) => `/model/${projectId}`,
  (projectId) => `/model/${projectId}`,
  () => '/incentive',
]

export default function PipelineSteps({ projectId, currentStep }: PipelineStepsProps) {
  const navigate = useNavigate()

  return (
    <Card style={{ marginBottom: 16 }}>
      <Steps
        current={currentStep}
        items={HUB_STEP_TITLES.map((title, index) => ({
          title,
          description:
            index === currentStep ? (
              <Button
                size="small"
                type="link"
                style={{ paddingLeft: 0 }}
                onClick={() => navigate(STEP_TARGET_PATH[index](projectId))}
              >
                去完成 →
              </Button>
            ) : undefined,
        }))}
      />
    </Card>
  )
}
