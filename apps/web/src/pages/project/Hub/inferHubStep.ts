import { PROPOSAL_LATE_STAGE_STATUSES } from './constants'
import type { HubStepIndex, ProjectDashboardData } from './types'

const sumCnt = (rows: readonly { cnt: number }[]): number =>
  rows.reduce((total, row) => total + Number(row.cnt), 0)

/**
 * 从项目看板数据推断当前所处流水线步骤（0=上传 … 4=创效）。
 *
 * 关键取舍：看板端点不返回「模型是否已构建」的直接信号（工程模型模块的数据
 * 归属另一 agent 并行开发的 pages/model，本任务边界禁止读取其接口）。
 * 因此「工程建模」与「算量」两步用可得的间接信号近似判断：
 *   - 有已发布图纸但尚无任何提案 → 视为处于「工程建模/算量」阶段，
 *     取更早的「工程建模」为当前步，引导用户先去模型页查看/构建。
 *   - 一旦出现提案（哪怕是草稿/测算中）→ 视为已进入「算量」之后，
 *     当前步前移到「算量」。
 *   - 提案进入签字/公示/分配/兑现等后段状态 → 当前步为「创效提案」。
 * 该近似会在「已建好模型但还没做算量」与「还没建模」两种情况下都停在同一步，
 * 属于已知精度上限；待 D-04（跨模块状态标准化）打通模型构建状态后可收紧。
 */
export function inferHubStep(data: ProjectDashboardData): HubStepIndex {
  const totalDrawings = sumCnt(data.drawings_by_status)
  if (totalDrawings === 0) return 0

  const reviewedCount = data.ai_quality?.reviewed_count ?? 0
  const hasUnresolvedCritical = (data.ai_quality?.drawings_with_critical ?? 0) > 0
  if (reviewedCount < totalDrawings || hasUnresolvedCritical) return 1

  const publishedCnt =
    data.drawings_by_status.find((row) => row.status === 'published')?.cnt ?? 0
  if (publishedCnt === 0) return 1

  const totalProposals = sumCnt(data.proposal_funnel)
  if (totalProposals === 0) return 2

  const hasLateStageProposal = data.proposal_funnel.some(
    (row) => PROPOSAL_LATE_STAGE_STATUSES.includes(row.status) && row.cnt > 0,
  )
  if (hasLateStageProposal) return 4

  return 3
}
