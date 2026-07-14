/**
 * 审查中心（路由 /review 与 /projects/:id/review）— Phase D 泳道2 D-06
 *
 * 合并三处割裂的审查入口：单图 AI 审图 Tab、会审审查 Tab、套图审查独立页
 * （drawings/ReviewBatch）。三者本质都是「问题清单 + 状态流转」，统一到一个
 * Finding 抽象（D-05 后端聚合）之上，用 Tab（来源）+ 筛选（严重度/状态/图纸/
 * 套图批次）呈现，问题闭环走统一状态机而非各自为政的原生状态字段。
 *
 * 旧 /drawings/review-batches 路由改为重定向到本页（见 config/routes.ts），
 * ReviewBatch 页面文件本身保留不删。
 */
import { useParams } from '@umijs/max'
import ProjectSelector from './ProjectSelector'
import ReviewCenterBody from './ReviewCenterBody'

export default function ReviewCenter(): JSX.Element {
  const params = useParams<{ id?: string }>()
  const projectId = params.id ?? ''

  if (!projectId) {
    return <ProjectSelector />
  }

  return <ReviewCenterBody projectId={projectId} />
}
