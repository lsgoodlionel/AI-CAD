/**
 * 成果标记（markers）对齐 Fragments 世界（A-08，WS2）——纯工具模块。
 *
 * Phase A 只做**楼层级**对齐：markers 的 (x,y)∈[0,1] 归一化平面坐标按所在楼层的
 * 标高 + 平面尺寸映射到 Fragments/three.js 世界坐标（Y 向上）。构件级精确锚定见 Phase B。
 *
 * 坐标约定与现有 three.js 贴图/挤出场景（sceneBuilder.buildMarkerInstances）保持一致：
 *   worldX = center.x + (marker.x - 0.5) * planWidth
 *   worldZ = center.z + (marker.y - 0.5) * planDepth
 *   worldY = elevation + markerLift
 */
import type {
  ModelScene,
  SceneFloorV2,
  SceneMarker,
} from '@/services/projectModel'

/** 楼层在 Fragments 世界里的落位参数 */
export interface FragmentsFloorPlacement {
  floorKey: string
  /** 楼层标高（世界 Y，米） */
  elevation: number
  /** 归一化 x∈[0,1] 对应的世界 X 跨度（米） */
  planWidth: number
  /** 归一化 y∈[0,1] 对应的世界 Z 跨度（米） */
  planDepth: number
  /** 平面中心的世界 (x,z)，缺省 [0,0] */
  center?: [number, number]
  /** 标记抬升量（避免与楼板 z-fighting），缺省 0 */
  markerLift?: number
}

/** 对齐后的标记：保留原 marker + 世界坐标 */
export interface AlignedMarker {
  marker: SceneMarker
  position: { x: number; y: number; z: number }
}

/** 批量对齐结果：命中楼层的 aligned + 无楼层落位的 skipped */
export interface AlignMarkersResult {
  aligned: AlignedMarker[]
  skipped: SceneMarker[]
}

const DEFAULT_MARKER_LIFT = 0.2
const DEFAULT_PLAN_WIDTH = 20
const DEFAULT_PLAN_DEPTH = 14
const DEFAULT_STORY_HEIGHT = 4.5

/** 单个标记 → 世界坐标（纯函数） */
export function alignMarkerToFragments(
  marker: SceneMarker,
  placement: FragmentsFloorPlacement,
): AlignedMarker {
  const [cx, cz] = placement.center ?? [0, 0]
  const lift = placement.markerLift ?? DEFAULT_MARKER_LIFT
  return {
    marker,
    position: {
      x: cx + (marker.x - 0.5) * placement.planWidth,
      y: placement.elevation + lift,
      z: cz + (marker.y - 0.5) * placement.planDepth,
    },
  }
}

/**
 * 批量对齐：按 floor_key 找楼层落位；找不到的标记进入 skipped（不静默丢弃）。
 * @param placements 楼层落位表（floorKey → placement）
 */
export function alignMarkersToFragments(
  markers: SceneMarker[],
  placements: Map<string, FragmentsFloorPlacement>,
): AlignMarkersResult {
  const aligned: AlignedMarker[] = []
  const skipped: SceneMarker[] = []
  for (const marker of markers) {
    const placement = placements.get(marker.floor_key)
    if (!placement) {
      skipped.push(marker)
      continue
    }
    aligned.push(alignMarkerToFragments(marker, placement))
  }
  return { aligned, skipped }
}

export interface PlacementBuildOptions {
  /** 平面 X 跨度（米），缺省 20 */
  planWidth?: number
  /** 平面 Z 跨度（米），缺省 14 */
  planDepth?: number
  /** 平面中心世界坐标，缺省 [0,0] */
  center?: [number, number]
  /** 缺省层高（无真实标高时按 order 递推），缺省 4.5 */
  storyHeight?: number
  markerLift?: number
}

/**
 * 从 scene 楼层派生楼层落位表：标高优先取 `elevation_m`，缺失按 order 递推。
 * 用于把 `scene.markers` 叠加到 Fragments 世界（楼层级对齐）。
 */
export function buildFloorPlacements(
  scene: Pick<ModelScene, 'floors'>,
  options: PlacementBuildOptions = {},
): Map<string, FragmentsFloorPlacement> {
  const planWidth = options.planWidth ?? DEFAULT_PLAN_WIDTH
  const planDepth = options.planDepth ?? DEFAULT_PLAN_DEPTH
  const storyHeight = options.storyHeight ?? DEFAULT_STORY_HEIGHT
  const center = options.center ?? [0, 0]

  const sorted = [...scene.floors].sort((a, b) => a.order - b.order) as SceneFloorV2[]
  const placements = new Map<string, FragmentsFloorPlacement>()
  let running = 0
  sorted.forEach((floor, index) => {
    const real = floor.elevation_m
    const elevation =
      typeof real === 'number' ? real : index === 0 ? 0 : running + storyHeight
    running = elevation
    placements.set(floor.key, {
      floorKey: floor.key,
      elevation,
      planWidth,
      planDepth,
      center,
      markerLift: options.markerLift ?? DEFAULT_MARKER_LIFT,
    })
  })
  return placements
}
