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
  SceneMarker,
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

export interface FloorUserData {
  kind: 'floor'
  floorKey: string
}

export interface BuiltSceneGraph {
  root: THREE.Group
  floorMeshes: THREE.Mesh[]
  drawingMeshes: THREE.Mesh[]
  markerMeshes: THREE.Mesh[]
  /** 楼层 key → 板片中心 Y 坐标 */
  floorYByKey: Map<string, number>
  /** 场景整体高度（相机初始化用） */
  totalHeight: number
}

// ── 构建 ─────────────────────────────────────────────────────

function buildFloorBoard(floorKey: string, y: number): THREE.Mesh {
  const geometry = new THREE.BoxGeometry(FLOOR_WIDTH, FLOOR_THICKNESS, FLOOR_DEPTH)
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
): THREE.Mesh {
  const cols = Math.ceil(Math.sqrt(count))
  const rows = Math.ceil(count / cols)
  const cellW = FLOOR_WIDTH / cols
  const cellD = FLOOR_DEPTH / rows
  const panelW = Math.min(PANEL_MAX_WIDTH, cellW * 0.85)
  const panelH = Math.min(PANEL_MAX_HEIGHT, cellD * 0.85)

  const col = index % cols
  const row = Math.floor(index / cols)
  const x = -FLOOR_WIDTH / 2 + (col + 0.5) * cellW
  const z = -FLOOR_DEPTH / 2 + (row + 0.5) * cellD

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

function buildMarkerSphere(marker: SceneMarker, floorY: number): THREE.Mesh {
  const geometry = new THREE.SphereGeometry(MARKER_RADIUS, 16, 12)
  const material = new THREE.MeshLambertMaterial({
    color: SEVERITY_COLORS[marker.severity] ?? SEVERITY_COLORS.info,
  })
  const mesh = new THREE.Mesh(geometry, material)
  mesh.position.set(
    (marker.x - 0.5) * FLOOR_WIDTH,
    floorY + FLOOR_THICKNESS / 2 + MARKER_RADIUS + 0.05,
    (marker.y - 0.5) * FLOOR_DEPTH,
  )
  const userData: MarkerUserData = { kind: 'marker', marker }
  mesh.userData = userData
  return mesh
}

/** 由 scene JSON 构建完整 three.js 对象树（一次构建，后续仅切换可见性） */
export function buildSceneGraph(scene: ModelScene): BuiltSceneGraph {
  const root = new THREE.Group()
  const floorMeshes: THREE.Mesh[] = []
  const drawingMeshes: THREE.Mesh[] = []
  const markerMeshes: THREE.Mesh[] = []
  const floorYByKey = new Map<string, number>()

  const sorted = [...scene.floors].sort((a, b) => a.order - b.order)
  sorted.forEach((floor, level) => {
    const y = level * FLOOR_HEIGHT
    floorYByKey.set(floor.key, y)

    const board = buildFloorBoard(floor.key, y)
    floorMeshes.push(board)
    root.add(board)

    floor.drawings.forEach((drawing, index) => {
      const panel = buildDrawingPanel(drawing, index, floor.drawings.length, floor.key, y)
      drawingMeshes.push(panel)
      root.add(panel)
    })
  })

  scene.markers.forEach((marker) => {
    const floorY = floorYByKey.get(marker.floor_key)
    if (floorY === undefined) return
    const sphere = buildMarkerSphere(marker, floorY)
    markerMeshes.push(sphere)
    root.add(sphere)
  })

  return {
    root,
    floorMeshes,
    drawingMeshes,
    markerMeshes,
    floorYByKey,
    totalHeight: Math.max(sorted.length - 1, 0) * FLOOR_HEIGHT,
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
  })
}
