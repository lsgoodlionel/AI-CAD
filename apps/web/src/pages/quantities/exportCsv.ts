/**
 * 算量中心 CSV 导出（客户端生成，无需后端端点）。
 * 汇总项目级 + 分楼层/分单体 QTO 口径为一张宽表，统一导出入口。
 */
import type { ProjectQtoData, QtoFloorSummary, QtoBuildingSummary, QtoSummary } from '@/services/quantities'

const CSV_HEADER = [
  '范围类型', '范围标识', '混凝土净体积(m³)', '混凝土毛体积(m³)',
  '模板接触面积(m²)', '构件总数', '实测构件数', '估算构件数', '未覆盖构件数',
  '钢筋量(t)',
]

function escapeCsvCell(value: string | number): string {
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function summaryRow(scopeType: string, scopeKey: string, summary: QtoSummary): string {
  const cells = [
    scopeType,
    scopeKey,
    summary.concrete.net_m3,
    summary.concrete.gross_m3,
    summary.formwork.contact_m2,
    summary.element_count,
    summary.measured_count,
    summary.estimated_count,
    summary.uncovered_count,
    summary.rebar?.total_t ?? '',
  ]
  return cells.map(escapeCsvCell).join(',')
}

export function buildQuantitiesCsv(data: ProjectQtoData): string {
  const rows = [CSV_HEADER.join(',')]
  rows.push(summaryRow('项目汇总', '全项目', data.project))
  data.by_floor.forEach((floor: QtoFloorSummary) => {
    rows.push(summaryRow('楼层', floor.floor_label || floor.floor_key, floor))
  })
  data.by_building.forEach((building: QtoBuildingSummary) => {
    rows.push(summaryRow('单体', building.building_key, building))
  })
  return `﻿${rows.join('\n')}`
}

export function downloadQuantitiesCsv(data: ProjectQtoData, projectId: string): void {
  const csv = buildQuantitiesCsv(data)
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `算量汇总_${projectId}_${Date.now()}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
