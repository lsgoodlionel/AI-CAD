/**
 * 语义树修正操作（预览影响范围 + 提交，含乐观并发版本过期处理）。
 * 从 useModelWorkspaceState.ts 拆出，只依赖 projectId 与 fetchModel 回调。
 */
import { useCallback } from 'react'
import { message } from 'antd'
import { applyProjectModelSemanticOperation, previewProjectModelSemanticImpact } from '@/services/projectModel'
import type { SemanticOperationDraft, SemanticOperationOutcome, SemanticOperationPreview } from './types'
import { readErrorNumber, readErrorString, semanticOperationPayload } from './workspaceHelpers'
import type { RequestLikeError } from './workspaceHelpers'

export function useSemanticOperations(projectId: string, fetchModel: () => Promise<void>) {
  const handlePreviewSemanticOperation = useCallback(async (
    draft: SemanticOperationDraft,
  ): Promise<SemanticOperationPreview> => {
    return previewProjectModelSemanticImpact(projectId, semanticOperationPayload(draft))
  }, [projectId])

  const handleSubmitSemanticOperation = useCallback(async (
    draft: SemanticOperationDraft,
  ): Promise<SemanticOperationOutcome> => {
    try {
      await applyProjectModelSemanticOperation(projectId, semanticOperationPayload(draft))
      await fetchModel()
      message.success('语义修正已提交')
      return { ok: true }
    } catch (error) {
      const staleVersion = readErrorNumber(error, 'version', 'expected_version', 'expectedVersion')
      if ((error as RequestLikeError)?.response?.status === 409 && staleVersion) {
        message.warning('语义树版本已更新，请刷新后重试')
        return {
          ok: false,
          staleVersion,
          message: readErrorString(error, 'message') ?? '语义树版本已更新',
        }
      }
      message.error(readErrorString(error, 'message') ?? '语义修正失败')
      return {
        ok: false,
        message: readErrorString(error, 'message') ?? '语义修正失败',
      }
    }
  }, [fetchModel, projectId])

  return { handlePreviewSemanticOperation, handleSubmitSemanticOperation }
}
