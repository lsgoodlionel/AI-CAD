/**
 * IFC/Fragments 构件拾取子状态：高亮 + 解析属性 + 清除。从 useModelWorkspaceState.ts
 * 拆出（原 A-08 接线逻辑），自成一体，只依赖当前渲染模式。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { PickedFragmentItem } from '@/services/projectModel'
import type { FragmentPickResult, FragmentsCameraPose, FragmentsSceneHandle } from './FragmentsScene'
import { resolvePickedItem } from './fragmentsPicking'
import type { ModelViewMode } from './sceneBuilder'

export function useFragmentSelection(viewMode: ModelViewMode) {
  const fragmentsSceneRef = useRef<FragmentsSceneHandle>(null)
  /** 拾取请求令牌：连点/清除时旧的 resolvePickedItem 迟到不覆盖新状态。 */
  const pickRequestRef = useRef(0)
  const fragmentsCameraPose = useRef<FragmentsCameraPose | null>(null)
  const [fragmentItem, setFragmentItem] = useState<PickedFragmentItem | null>(null)
  const [fragmentItemLoading, setFragmentItemLoading] = useState(false)

  const handleFragmentPick = useCallback((pick: FragmentPickResult | null) => {
    const sceneHandle = fragmentsSceneRef.current
    if (!sceneHandle) return
    pickRequestRef.current += 1
    const requestToken = pickRequestRef.current
    if (!pick) {
      void sceneHandle.clearHighlight().catch(() => {})
      setFragmentItem(null)
      setFragmentItemLoading(false)
      return
    }
    void sceneHandle.highlight([pick.localId]).catch(() => {})
    const fragModel = sceneHandle.getModel()
    if (!fragModel) return
    setFragmentItemLoading(true)
    resolvePickedItem(fragModel, pick.localId)
      .then((resolved) => {
        if (pickRequestRef.current !== requestToken) return
        setFragmentItem(resolved)
      })
      .catch(() => {
        if (pickRequestRef.current !== requestToken) return
        setFragmentItem(null)
      })
      .finally(() => {
        if (pickRequestRef.current !== requestToken) return
        setFragmentItemLoading(false)
      })
  }, [])

  // 离开 IFC 模式清空选中构件，避免残留
  useEffect(() => {
    if (viewMode !== 'ifc') setFragmentItem(null)
  }, [viewMode])

  /** 属性面板「清除」按钮：令牌+1 防迟到覆盖，清空高亮与选中构件。 */
  const handleClearFragmentSelection = useCallback(() => {
    pickRequestRef.current += 1
    setFragmentItem(null)
    void fragmentsSceneRef.current?.clearHighlight().catch(() => {})
  }, [])

  return {
    fragmentsSceneRef,
    fragmentsCameraPose,
    fragmentItem,
    fragmentItemLoading,
    handleFragmentPick,
    handleClearFragmentSelection,
  }
}
