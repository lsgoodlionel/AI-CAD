/**
 * 构件图层筛选项（浏览模式「构件图层」面板 + 算量模式「构件高亮」共用）。
 * 从原 index.tsx 顶层同名函数原样迁出。
 */
import type { ModelScene, SceneFloorV2 } from '@/services/projectModel'

export const ELEMENT_TYPE_LABEL: Record<string, string> = {
  columns: '柱',
  walls: '墙',
  beams: '梁',
  slabs: '板',
  equipment: '设备',
}

export function elementFilterOptions(scene: ModelScene): { label: string; value: string }[] {
  const systems = new Set<string>()
  let hasAxes = false
  for (const floor of scene.floors as SceneFloorV2[]) {
    for (const pipe of floor.elements?.pipes ?? []) systems.add(pipe.system)
    if (floor.axes && (floor.axes.x?.length || floor.axes.y?.length)) hasAxes = true
  }
  return [
    ...['columns', 'walls', 'beams', 'slabs'].map((kind) => ({
      label: ELEMENT_TYPE_LABEL[kind], value: kind,
    })),
    ...Array.from(systems).map((system) => ({
      label: `管线·${system}`, value: `pipes:${system}`,
    })),
    { label: ELEMENT_TYPE_LABEL.equipment, value: 'equipment' },
    { label: '外观壳体', value: 'shell' },
    // E2 轴网层：scene 携带轴网数据时才出现（识别出的轴线位置+轴号）
    ...(hasAxes ? [{ label: '轴网', value: 'axes' }] : []),
  ]
}
