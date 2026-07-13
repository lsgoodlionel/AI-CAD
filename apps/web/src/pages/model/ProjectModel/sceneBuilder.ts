/**
 * three.js 场景构建辅助（纯构建逻辑，不含 React / 渲染循环）
 * 布局约定（蓝图第 8 节模块 E）：
 * - 楼层 = 半透明 BoxGeometry 板片按 order 升序堆叠，层高 3 单位，板厚 0.3，平面 20×14
 * - 图纸 = 楼层上方 PlaneGeometry 网格排布，有贴图异步贴 TextureLoader，无贴图画 EdgesGeometry 线框
 * - 标记 = SphereGeometry(r=0.18)，severity 着色，(x,y)∈[0,1] 映射到板片平面
 */
import * as THREE from 'three'
import type {
  ModelScene,
  SceneDrawing,
  SceneFloorV2,
  SceneMarker,
  SceneModelIfc,
} from '@/services/projectModel'

// ── 布局常量 ─────────────────────────────────────────────────

export const FLOOR_WIDTH = 20
export const FLOOR_DEPTH = 14
export const FLOOR_THICKNESS = 0.3
export const FLOOR_HEIGHT = 3
export const MARKER_RADIUS = 0.18
export const FLOOR_OPACITY = 0.32
export const FLOOR_FADED_OPACITY = 0.06
export const PANEL_MAX_WIDTH = 4.2
export const PANEL_MAX_HEIGHT = 3
export const PANEL_LIFT = 1.4

export const SEVERITY_COLORS: Record<string, string> = {
  critical: '#f5222d',
  major: '#fa8c16',
  minor: '#faad14',
  info: '#8c8c8c',
}

// ── 渲染模式分派（A-07）─────────────────────────────────────
//
// scene.model_ifc 由后端 A-03/A-04 写入（见 docs/MODEL_BASE_BLUEPRINT.md 第 4 节），
// 但 services/projectModel.ts 的 ModelScene 未声明该字段（该文件由并行 A-08 拥有），
// 故此处就地窄化读取，旧数据缺省安全返回 null。SceneModelIfc 类型统一由
// services/projectModel.ts 提供（唯一来源）。

/** 顶层渲染模式：ifc(Fragments) 优先，回退现有 elements(挤出)/texture(贴图)/mixed。 */
export type ModelViewMode = 'ifc' | 'elements' | 'texture' | 'mixed'

/** 从 scene 读取 model_ifc（缺省安全返回 null）。 */
export function readModelIfc(scene: ModelScene): SceneModelIfc | null {
  const raw = (scene as { model_ifc?: unknown }).model_ifc
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const ifcKey = typeof obj.ifc_key === 'string' ? obj.ifc_key : ''
  const fragKey =
    typeof obj.frag_key === 'string' && obj.frag_key.trim() ? obj.frag_key : null
  const buildMode = obj.build_mode
  return {
    ifc_key: ifcKey,
    frag_key: fragKey,
    build_mode:
      buildMode === 'ifc' || buildMode === 'elements' || buildMode === 'texture'
        ? buildMode
        : 'texture',
    is_estimated: obj.is_estimated === true,
    generated_at: typeof obj.generated_at === 'string' ? obj.generated_at : undefined,
  }
}

/** 默认渲染模式：有 frag_key → ifc；否则 V2 → mixed；V1 → texture。 */
export function pickDefaultViewMode(scene: ModelScene): ModelViewMode {
  if (readModelIfc(scene)?.frag_key) return 'ifc'
  return scene.schema_version === 2 ? 'mixed' : 'texture'
}

// eslint-disable-next-line import/no-cycle -- elementsBuilder 仅引用本文件常量
import {
  buildFloorElementMeshes,
  buildFloorShell,
  elementsBounds,
} from './elementsBuilder'

const FLOOR_COLOR = '#5b8ff9'
const PANEL_PLACEHOLDER_COLOR = '#1677ff'
const PANEL_EDGE_COLOR = '#69b1ff'
export const HIGHLIGHT_COLOR = '#13c2c2'

// ── userData 元数据（filters 变化仅更新可见性时读取）─────────

export interface DrawingUserData {
  kind: 'drawing'
  drawing: SceneDrawing
  floorKey: string
}

export interface MarkerUserData {
  kind: 'marker'
  marker: SceneMarker
}

export interface MarkerInstancesUserData {
  kind: 'markerInstances'
}

/**
 * 标记合批（InstancedMesh）：1500+ 问题标记共享一份球体 geometry/material，
 * 仅逐实例矩阵（位置）与颜色（severity）不同。相比逐球独立 Mesh，draw call 与
 * geometry/material 对象数从 N 降到 1，是本页浏览器内存与渲染的主要优化点。
 * - markers 与实例下标一一对齐（拾取用 instanceId 反查）
 * - baseMatrices 为各实例的「显示」矩阵，隐藏时写入零缩放矩阵（退化三角形不参与光栅/拾取）
 */
export interface MarkerInstances {
  mesh: THREE.InstancedMesh
  markers: SceneMarker[]
  baseMatrices: THREE.Matrix4[]
}

export interface FloorUserData {
  kind: 'floor'
  floorKey: string
}

export interface BuiltSceneGraph {
  root: THREE.Group
  floorMeshes: THREE.Mesh[]
  drawingMeshes: THREE.Mesh[]
  /** V2 构件网格（userData.kind === 'element'） */
  elementMeshes: THREE.Mesh[]
  /** 问题标记合批（InstancedMesh）；无可放置标记时为 null */
  markerInstances: MarkerInstances | null
  /** 楼层 key → 板片中心 Y 坐标 */
  floorYByKey: Map<string, number>
  /** 场景整体高度（相机初始化用） */
  totalHeight: number
  /** 取景半径（真实坐标模式≈建筑包络，抽象模式为旧默认） */
  fitRadius: number
  /** 是否真实坐标模式（1 unit = 1 米，楼层用真实标高） */
  realScale: boolean
}

// ── 构建 ─────────────────────────────────────────────────────

function buildFloorBoard(
  floorKey: string, y: number,
  floorW = FLOOR_WIDTH, floorD = FLOOR_DEPTH,
): THREE.Mesh {
  const geometry = new THREE.BoxGeometry(floorW, FLOOR_THICKNESS, floorD)
  const material = new THREE.MeshLambertMaterial({
    color: FLOOR_COLOR,
    transparent: true,
    opacity: FLOOR_OPACITY,
    depthWrite: false,
  })
  const mesh = new THREE.Mesh(geometry, material)
  mesh.position.set(0, y, 0)
  const userData: FloorUserData = { kind: 'floor', floorKey }
  mesh.userData = userData
  return mesh
}

/** 每层多张图时按 index 网格排布，返回图纸面板（水平放置于板片上方） */
function buildDrawingPanel(
  drawing: SceneDrawing,
  index: number,
  count: number,
  floorKey: string,
  floorY: number,
  floorW = FLOOR_WIDTH,
  floorD = FLOOR_DEPTH,
  panelScale = 1,
): THREE.Mesh {
  const cols = Math.ceil(Math.sqrt(count))
  const rows = Math.ceil(count / cols)
  const cellW = floorW / cols
  const cellD = floorD / rows
  const panelW = Math.min(PANEL_MAX_WIDTH * panelScale, cellW * 0.85)
  const panelH = Math.min(PANEL_MAX_HEIGHT * panelScale, cellD * 0.85)

  const col = index % cols
  const row = Math.floor(index / cols)
  const x = -floorW / 2 + (col + 0.5) * cellW
  const z = -floorD / 2 + (row + 0.5) * cellD

  const geometry = new THREE.PlaneGeometry(panelW, panelH)
  const hasImage = drawing.image_key !== ''
  const material = new THREE.MeshBasicMaterial({
    color: hasImage ? '#ffffff' : PANEL_PLACEHOLDER_COLOR,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: hasImage ? 0.95 : 0.12,
  })
  const mesh = new THREE.Mesh(geometry, material)
  mesh.rotation.x = -Math.PI / 2
  mesh.position.set(x, floorY + PANEL_LIFT, z)
  const userData: DrawingUserData = { kind: 'drawing', drawing, floorKey }
  mesh.userData = userData

  if (!hasImage) {
    // 无贴图：EdgesGeometry 线框占位
    const edges = new THREE.EdgesGeometry(geometry)
    const line = new THREE.LineSegments(
      edges,
      new THREE.LineBasicMaterial({ color: PANEL_EDGE_COLOR }),
    )
    mesh.add(line)
  }
  return mesh
}

/** 隐藏实例：零缩放矩阵（退化三角形，既不光栅也不被拾取） */
const MARKER_HIDDEN_MATRIX = new THREE.Matrix4().makeScale(0, 0, 0)

/**
 * 构建全部问题标记的 InstancedMesh（合批）。跳过所在楼层缺失的标记；
 * 无可放置标记时返回 null。markers/baseMatrices 与实例下标对齐。
 */
export function buildMarkerInstances(
  markers: SceneMarker[],
  floorYByKey: Map<string, number>,
  floorW = FLOOR_WIDTH,
  floorD = FLOOR_DEPTH,
  radius = MARKER_RADIUS,
): MarkerInstances | null {
  const placeable: SceneMarker[] = []
  const baseMatrices: THREE.Matrix4[] = []
  const colors: THREE.Color[] = []
  for (const marker of markers) {
    const floorY = floorYByKey.get(marker.floor_key)
    if (floorY === undefined) continue
    baseMatrices.push(
      new THREE.Matrix4().makeTranslation(
        (marker.x - 0.5) * floorW,
        floorY + FLOOR_THICKNESS / 2 + radius + 0.05,
        (marker.y - 0.5) * floorD,
      ),
    )
    colors.push(new THREE.Color(SEVERITY_COLORS[marker.severity] ?? SEVERITY_COLORS.info))
    placeable.push(marker)
  }
  if (!placeable.length) return null

  const geometry = new THREE.SphereGeometry(radius, 12, 8)
  const material = new THREE.MeshLambertMaterial()
  const mesh = new THREE.InstancedMesh(geometry, material, placeable.length)
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage) // filters 变化时逐实例更新矩阵
  for (let i = 0; i < placeable.length; i += 1) {
    mesh.setMatrixAt(i, baseMatrices[i])
    mesh.setColorAt(i, colors[i])
  }
  mesh.instanceMatrix.needsUpdate = true
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  const userData: MarkerInstancesUserData = { kind: 'markerInstances' }
  mesh.userData = userData
  return { mesh, markers: placeable, baseMatrices }
}

/** 按谓词逐实例切换标记可见性（显示=还原矩阵，隐藏=零缩放矩阵） */
export function applyMarkerVisibility(
  inst: MarkerInstances,
  isVisible: (marker: SceneMarker) => boolean,
): void {
  for (let i = 0; i < inst.markers.length; i += 1) {
    inst.mesh.setMatrixAt(
      i,
      isVisible(inst.markers[i]) ? inst.baseMatrices[i] : MARKER_HIDDEN_MATRIX,
    )
  }
  inst.mesh.instanceMatrix.needsUpdate = true
}

/** 全场景构件包围盒（跨楼层聚合）；无任何构件坐标 → null */
function collectSceneBounds(
  floors: ModelScene['floors'],
): { minX: number; maxX: number; minY: number; maxY: number } | null {
  let acc: { minX: number; maxX: number; minY: number; maxY: number } | null = null
  for (const floor of floors as SceneFloorV2[]) {
    if (!floor.elements) continue
    const bounds = elementsBounds(floor.elements)
    if (!bounds) continue
    acc = acc
      ? {
          minX: Math.min(acc.minX, bounds.minX),
          maxX: Math.max(acc.maxX, bounds.maxX),
          minY: Math.min(acc.minY, bounds.minY),
          maxY: Math.max(acc.maxY, bounds.maxY),
        }
      : bounds
  }
  return acc
}

const DEFAULT_STORY_M = 4.5
const STORY_RANGE_M: [number, number] = [3, 12]

/** 楼层真实标高链：elevation_m 优先，缺失按上层 + 缺省层高递推 */
function floorElevations(sorted: SceneFloorV2[]): number[] {
  const ys: number[] = []
  sorted.forEach((floor, index) => {
    const real = floor.elevation_m
    if (typeof real === 'number') {
      ys.push(real)
    } else {
      ys.push(index === 0 ? 0 : ys[index - 1] + DEFAULT_STORY_M)
    }
  })
  return ys
}

/** 由 scene JSON 构建完整 three.js 对象树（一次构建，后续仅切换可见性） */
export function buildSceneGraph(scene: ModelScene): BuiltSceneGraph {
  const root = new THREE.Group()
  const floorMeshes: THREE.Mesh[] = []
  const drawingMeshes: THREE.Mesh[] = []
  const elementMeshes: THREE.Mesh[] = []
  const floorYByKey = new Map<string, number>()
  const renderElements = scene.schema_version === 2

  const sorted = [...scene.floors].sort((a, b) => a.order - b.order) as SceneFloorV2[]
  const bounds = renderElements ? collectSceneBounds(sorted) : null
  const realScale = bounds !== null

  // 真实坐标模式：1 unit=1 米，板片=建筑包络，楼层 Y=真实标高
  const center: [number, number] = bounds
    ? [(bounds.minX + bounds.maxX) / 2, (bounds.minY + bounds.maxY) / 2]
    : [0, 0]
  const floorW = bounds ? Math.max(bounds.maxX - bounds.minX + 4, 12) : FLOOR_WIDTH
  const floorD = bounds ? Math.max(bounds.maxY - bounds.minY + 4, 12) : FLOOR_DEPTH
  const elevations = realScale
    ? floorElevations(sorted)
    : sorted.map((_f, level) => level * FLOOR_HEIGHT)
  const markerRadius = realScale ? Math.max(floorW, floorD) / 90 : MARKER_RADIUS
  const panelScale = realScale ? Math.max(floorW / FLOOR_WIDTH, 1) : 1

  sorted.forEach((floor, level) => {
    const y = elevations[level]
    floorYByKey.set(floor.key, y)
    const nextY = elevations[level + 1]
    const storyH = realScale
      ? Math.min(Math.max((nextY ?? y + DEFAULT_STORY_M) - y, STORY_RANGE_M[0]), STORY_RANGE_M[1])
      : FLOOR_HEIGHT

    const board = buildFloorBoard(floor.key, y, floorW, floorD)
    floorMeshes.push(board)
    root.add(board)

    floor.drawings.forEach((drawing, index) => {
      const panel = buildDrawingPanel(
        drawing, index, floor.drawings.length, floor.key, y, floorW, floorD, panelScale,
      )
      drawingMeshes.push(panel)
      root.add(panel)
    })

    // ── V2 构件层（有构件的楼层叠加渲染；构建失败/超预算自动回退贴图）──
    const elements = floor.elements
    if (renderElements && elements) {
      const meshes = buildFloorElementMeshes(
        elements, y, floor.key, 'main',
        realScale ? { center, storyHeight: storyH } : undefined,
      )
      if (meshes) {
        elementMeshes.push(...meshes)
        meshes.forEach((mesh) => root.add(mesh))
      }
      if (realScale) {
        // 建筑外观壳体（楼层轮廓放样至层顶，独立图层可开关）
        const shell = buildFloorShell(elements, y, storyH, center, floor.key, 'main')
        if (shell) {
          elementMeshes.push(shell)
          root.add(shell)
        }
      }
    }
  })

  const markerInstances = buildMarkerInstances(
    scene.markers, floorYByKey, floorW, floorD, markerRadius,
  )
  if (markerInstances) root.add(markerInstances.mesh)

  const minY = Math.min(...elevations, 0)
  const maxY = Math.max(...elevations, 0)
  return {
    root,
    floorMeshes,
    drawingMeshes,
    elementMeshes,
    markerInstances,
    floorYByKey,
    totalHeight: maxY - minY,
    fitRadius: realScale
      ? Math.max(floorW, floorD, maxY - minY) * 0.75
      : Math.max(FLOOR_WIDTH, sorted.length * FLOOR_HEIGHT) * 0.9,
    realScale,
  }
}

/** 为焦点图纸面板生成高亮描边（LineSegments，附着于面板局部坐标系） */
export function buildHighlightOutline(panel: THREE.Mesh): THREE.LineSegments {
  const edges = new THREE.EdgesGeometry(panel.geometry)
  const line = new THREE.LineSegments(
    edges,
    new THREE.LineBasicMaterial({ color: HIGHLIGHT_COLOR, linewidth: 2 }),
  )
  line.name = 'focus-highlight'
  return line
}

// ── 资源释放 ─────────────────────────────────────────────────

function disposeMaterial(material: THREE.Material): void {
  const withMap = material as THREE.Material & { map?: THREE.Texture | null }
  if (withMap.map) withMap.map.dispose()
  material.dispose()
}

/** 递归释放对象树上的 geometry / material / texture */
export function disposeObjectTree(object: THREE.Object3D): void {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh
    if (mesh.geometry) mesh.geometry.dispose()
    if (mesh.material) {
      if (Array.isArray(mesh.material)) {
        mesh.material.forEach(disposeMaterial)
      } else {
        disposeMaterial(mesh.material)
      }
    }
    // InstancedMesh 还需释放 instanceMatrix / instanceColor GPU 缓冲
    const inst = child as THREE.InstancedMesh
    if ((inst as unknown as { isInstancedMesh?: boolean }).isInstancedMesh) inst.dispose()
  })
}
