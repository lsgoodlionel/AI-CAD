/**
 * 项目工作台文案常量：状态/动作中文映射 + localStorage key
 * 与 pages/dashboard/ProjectDashboard、pages/drawings/DrawingList 的映射表保持一致，
 * 各页面独立维护（未提取公共 utils，避免跨越本任务的文件边界）。
 */

export const LAST_PROJECT_STORAGE_KEY = 'cad_last_project'

export const DRAWING_STATUS_LABEL: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  ai_reviewing: { label: 'AI审图中', color: 'processing' },
  ai_done: { label: '审图完成', color: 'warning' },
  technical_review: { label: '一审中', color: 'blue' },
  economic_review: { label: '二审中', color: 'purple' },
  settlement_review: { label: '三审中', color: 'orange' },
  published: { label: '已发布', color: 'success' },
  rejected: { label: '已驳回', color: 'error' },
}

export const PROPOSAL_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  calculating: '测算中',
  pending_sign: '待签字',
  public_notice: '公示中',
  distributing: '分配中',
  approved: '已审批',
  paid: '已兑现',
  rejected: '已驳回',
}

/** 提案漏斗中处于「已进入签字/公示/分配/兑现」的后段状态，用于步骤推断 */
export const PROPOSAL_LATE_STAGE_STATUSES: readonly string[] = [
  'pending_sign', 'public_notice', 'distributing', 'approved', 'paid',
]

export const ACTIVITY_ACTION_LABEL: Record<string, string> = {
  upload_drawing: '上传图纸',
  approve_technical: '一审通过',
  sign_economic: '经济师签字',
  publish_drawing: '发布图纸',
  submit_proposal: '提交提案',
  approve_proposal: '审批提案',
}

export const HUB_STEP_TITLES: readonly string[] = [
  '上传图纸', 'AI审查', '工程建模', '算量', '创效提案',
]
