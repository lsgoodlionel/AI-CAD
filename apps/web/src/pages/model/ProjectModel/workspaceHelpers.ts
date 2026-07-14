/**
 * ModelWorkspace 纯工具函数（错误解析 / 人工标注合并 / 语义操作载荷），
 * 从原 index.tsx 顶层同名函数原样迁出，供 useModelWorkspaceState.ts 使用。
 */
import { request } from '@umijs/max'
import type { SemanticOperationRequest } from '@/services/projectModel'
import type {
  AnnotationQueueItem, AnnotationSaveDraft, BuildingUnitOption,
  LodModeOption, ModelLodMode, SemanticOperationDraft,
} from './types'

export interface RequestLikeError {
  response?: { status?: number, data?: Record<string, unknown> }
  data?: Record<string, unknown>
  info?: Record<string, unknown>
}

export function isNotBuiltError(error: unknown): boolean {
  return (error as RequestLikeError)?.response?.status === 404
}

export function readErrorNumber(error: unknown, ...keys: string[]): number | undefined {
  const data = (error as RequestLikeError)?.data
  const info = (error as RequestLikeError)?.info
  const responseData = (error as RequestLikeError)?.response?.data
  const sources = [
    data,
    data?.detail as Record<string, unknown> | undefined,
    (data?.detail as Record<string, unknown> | undefined)?.latest as Record<string, unknown> | undefined,
    info,
    info?.detail as Record<string, unknown> | undefined,
    (info?.detail as Record<string, unknown> | undefined)?.latest as Record<string, unknown> | undefined,
    responseData,
    responseData?.detail as Record<string, unknown> | undefined,
    (responseData?.detail as Record<string, unknown> | undefined)?.latest as Record<string, unknown> | undefined,
  ]
  for (const source of sources) {
    if (!source) continue
    for (const key of keys) {
      const value = source[key]
      if (typeof value === 'number' && Number.isFinite(value)) return value
      if (typeof value === 'string' && value.trim()) {
        const parsed = Number(value)
        if (Number.isFinite(parsed)) return parsed
      }
    }
  }
  return undefined
}

export function readErrorString(error: unknown, ...keys: string[]): string | undefined {
  const data = (error as RequestLikeError)?.data
  const info = (error as RequestLikeError)?.info
  const responseData = (error as RequestLikeError)?.response?.data
  const sources = [
    data,
    data?.detail as Record<string, unknown> | undefined,
    info,
    info?.detail as Record<string, unknown> | undefined,
    responseData,
    responseData?.detail as Record<string, unknown> | undefined,
  ]
  for (const source of sources) {
    if (!source) continue
    for (const key of keys) {
      const value = source[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
  }
  return undefined
}

function normalizeManualKey(input: string): string {
  const value = input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return value || `manual-${Date.now()}`
}

export function mergeManualBuildingUnit(
  current: BuildingUnitOption[],
  draft: AnnotationSaveDraft,
): BuildingUnitOption[] {
  if (!draft.buildingUnitName.trim()) return current
  const existing = current.find((unit) => unit.label === draft.buildingUnitName.trim())
  if (existing) return current
  const manualUnit: BuildingUnitOption = {
    key: draft.buildingUnitKey ?? normalizeManualKey(draft.buildingUnitName),
    label: draft.buildingUnitName.trim(),
    source: 'manual',
    hasGeometry: false,
  }
  return [...current, manualUnit].sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
}

export async function saveModelAnnotation(
  projectId: string,
  item: AnnotationQueueItem,
  draft: AnnotationSaveDraft,
) {
  const payload = {
    drawing_id: item.drawingId,
    building_unit_key: draft.buildingUnitKey,
    building_unit_name: draft.buildingUnitName.trim(),
    story_key: draft.storyKey,
    story_name: draft.storyName.trim(),
    drawing_type: draft.drawingType.trim(),
  }
  const endpoints = [
    `/api/v1/projects/${projectId}/model/annotations`,
    `/api/v1/projects/${projectId}/model/annotation-queue`,
  ]

  for (let index = 0; index < endpoints.length; index += 1) {
    try {
      await request(endpoints[index], {
        method: 'POST',
        data: payload,
        skipErrorHandler: true,
      })
      return
    } catch (error) {
      const status = (error as RequestLikeError)?.response?.status
      if (status === 404 && index < endpoints.length - 1) continue
      throw error
    }
  }
}

export function semanticOperationPayload(
  draft: SemanticOperationDraft,
): SemanticOperationRequest {
  return {
    operation: draft.operation,
    node_id: draft.nodeId,
    version: draft.version,
    target_node_id: draft.targetNodeId,
    new_name: draft.newName,
    split_names: draft.splitNames,
  }
}

export function activeLodMode(
  lodModes: LodModeOption[],
  lodMode: ModelLodMode,
): LodModeOption {
  return lodModes.find((item) => item.key === lodMode)
    ?? lodModes.find((item) => item.enabled)
    ?? lodModes[0]
}
