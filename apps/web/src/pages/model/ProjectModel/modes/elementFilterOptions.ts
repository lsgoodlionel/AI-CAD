/**
 * 构件图层筛选项（浏览模式「构件图层」面板 + 算量模式「构件高亮」共用）。
 * 从原 index.tsx 顶层同名函数原样迁出。
 *
 * `unverified`：服务端 `element_validation` 报出的**没通过金标准**的构件类
 * （管线/设备 0%）。它们照样列出，但标「未验证」并默认不勾选 —— 别让它们
 * 以「识别结果」的面目出现在模型里，想看仍可以勾上。
 */
import type { ModelScene, SceneFloorV2 } from '@/services/projectModel'

export const ELEMENT_TYPE_LABEL: Record<string, string> = {
  columns: '柱',
  walls: '墙',
  beams: '梁',
  slabs: '板',
  equipment: '设备',
}

const UNVERIFIED_SUFFIX = '（未验证）'

/** 筛选值（如 `pipes:给排水`）所属的构件类是否未通过金标准。 */
export function isUnverifiedValue(value: string, unverified: string[]): boolean {
  return unverified.includes(value.split(':')[0])
}

export function elementFilterOptions(
  scene: ModelScene,
  unverified: string[] = [],
): { label: string; value: string }[] {
  const systems = new Set<string>()
  let hasAxes = false
  for (const floor of scene.floors as SceneFloorV2[]) {
    for (const pipe of floor.elements?.pipes ?? []) systems.add(pipe.system)
    if (floor.axes && (floor.axes.x?.length || floor.axes.y?.length)) hasAxes = true
  }
  const mark = (label: string, value: string) =>
    ({ label: isUnverifiedValue(value, unverified) ? `${label}${UNVERIFIED_SUFFIX}` : label, value })
  return [
    ...['columns', 'walls', 'beams', 'slabs'].map((kind) => mark(ELEMENT_TYPE_LABEL[kind], kind)),
    ...Array.from(systems).map((system) => mark(`管线·${system}`, `pipes:${system}`)),
    mark(ELEMENT_TYPE_LABEL.equipment, 'equipment'),
    { label: '外观壳体', value: 'shell' },
    // E2 轴网层：scene 携带轴网数据时才出现（识别出的轴线位置+轴号）
    ...(hasAxes ? [{ label: '轴网', value: 'axes' }] : []),
  ]
}

/** 默认勾选：全部，除了未通过金标准的构件类。 */
export function defaultElementFilter(
  options: { value: string }[],
  unverified: string[],
): string[] {
  return options.map((o) => o.value).filter((value) => !isUnverifiedValue(value, unverified))
}
