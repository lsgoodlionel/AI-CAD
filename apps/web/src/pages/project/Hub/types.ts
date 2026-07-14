/**
 * 项目工作台共享类型
 * 数据形状对应后端 GET /api/v1/dashboard/project/{projectId}（services/dashboard.ts）
 */

export interface ProjectSummary {
  id: string
  name: string
  code?: string
}

export interface DrawingStatusCount {
  status: string
  cnt: number
}

export interface AiQualitySummary {
  reviewed_count: number
  avg_issues: number
  total_critical: number
  drawings_with_critical: number
}

export interface StageDistributionRow {
  discipline: string
  published_cnt: number
  rejected_cnt: number
  total_cnt: number
}

export interface ProposalFunnelRow {
  status: string
  cnt: number
  total_saving: number
}

export interface RecentActivityRow {
  action: string
  resource: string
  resource_id: string
  new_state?: string | null
  created_at: string
  operator: string
}

export interface ProjectInfo {
  id: string
  name: string
  annual_output?: number
  status?: string
}

export interface ProjectDashboardData {
  project: ProjectInfo
  drawings_by_status: DrawingStatusCount[]
  ai_quality: AiQualitySummary
  stage_distribution: StageDistributionRow[]
  proposal_funnel: ProposalFunnelRow[]
  annual_saving_yuan: number
  kpi_red_flag: boolean
  recent_activity: RecentActivityRow[]
}

/** 流水线阶段索引：上传→审查→建模→算量→创效 */
export type HubStepIndex = 0 | 1 | 2 | 3 | 4
