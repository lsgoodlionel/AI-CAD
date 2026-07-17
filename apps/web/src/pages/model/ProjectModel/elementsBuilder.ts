/**
 * 构件级网格构建（schema_version=2，蓝图 MODEL_PRECISION_BLUEPRINT 第 5 节）
 *
 * - 构件米坐标 → 归一化映射到楼层板片（20×14）空间：同层统一缩放，比例真实；
 * - 柱/墙/梁/板 同类合并 BufferGeometry（性能）；设备保留独立 mesh（点击出 label）；
 * - 管线按 system 分组合并 TubeGeometry，按专业着色。
 */
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type {
  ElementEquipment,
  SceneFloorAxes,
  SceneFloorElements,
} from '@/services/projectModel'
import { FLOOR_DEPTH, FLOOR_HEIGHT, FLOOR_WIDTH } from './sceneBuilder'

// 构件配色（蓝图 5 节）
const ELEMENT_COLORS: Record<string, string> = {
  columns: '#8c8c8c',
  walls: '#bfbfbf',
  beams: '#d9d9d9',
  slabs: '#f0f0f0',
  equipment: '#722ed1',
}
const PIPE_SYSTEM_COLORS: Record<string, string> = {
  给排水: '#1890ff',
  电气: '#fa8c16',
  暖通: '#52c41a',
  消防: '#f5222d',
  其他: '#722ed1',
}

const ELEMENT_STORY_HEIGHT = FLOOR_HEIGHT * 0.85
const BEAM_TOP_OFFSET = 0.15
const SLAB_RENDER_THICKNESS = 0.12
const PIPE_RENDER_RADIUS = 0.06
const PLAN_MARGIN = 0.9 // 构件平面占板片比例
// 单层构件三角形预算（超出降级贴图由调用方处理）
export const ELEMENT_TRIANGLE_BUDGET = 100_000

/**
 * 设备合批网格的逐设备拾取索引：设备几何按顺序合并后，faceEnd 为该设备
 * 结束处的累计三角形数（exclusive）。命中 faceIndex 落在哪个区间即哪台设备。
 */
export interface EquipmentPick {
  faceEnd: number
  label?: string
  src?: string
}

export interface ElementUserData {
  kind: 'element'
  /** columns | walls | beams | slabs | equipment | pipes:<system> */
  elementType: string
  floorKey: string
  buildingKey: string
  count: number
  label?: string
  src?: string
  /** 反向追溯(G3):该合并网格构件的来源图纸 id 集合 */
  sourceDrawings?: string[]
  /** 识别途径集合(rule/circle/model/fused/human 等) */
  sourcePaths?: string[]
  /** 档案 OCR 反哺的类型标签(钢立柱/幕墙/围护桩…) */
  typeLabels?: string[]
  /** 仅设备合批网格：逐设备拾取区间（按 faceEnd 升序），供 raycast faceIndex 反查 */
  equipmentPicks?: EquipmentPick[]
}

/** 从构件集合聚合来源(反向追溯):distinct 来源图纸/识别途径/类型标签 */
export function collectSourceInfo(
  items: { src?: string; source?: string; type_label?: string; type_text?: string }[],
): Pick<ElementUserData, 'sourceDrawings' | 'sourcePaths' | 'typeLabels'> {
  const drawings = new Set<string>()
  const paths = new Set<string>()
  const types = new Set<string>()
  for (const it of items) {
    if (it.src) drawings.add(it.src)
    if (it.source) paths.add(it.source)
    if (it.type_label) types.add(it.type_text || it.type_label)
  }
  return {
    sourceDrawings: Array.from(drawings).slice(0, 50),
    sourcePaths: Array.from(paths),
    typeLabels: Array.from(types).slice(0, 10),
  }
}

/** 由命中的 faceIndex 反查是哪台设备（区间按 faceEnd 升序，取首个 faceIndex < faceEnd） */
export function resolveEquipmentPick(
  picks: EquipmentPick[],
  faceIndex: number,
): EquipmentPick | null {
  for (const pick of picks) {
    if (faceIndex < pick.faceEnd) return pick
  }
  return picks.length ? picks[picks.length - 1] : null
}

interface PlanTransform {
  toX: (x: number) => number
  toZ: (y: number) => number
  scale: number
}

/** 真实坐标渲染选项（Phase 7 V3：统一源坐标点，米=three 单位） */
export interface RealPlanOptions {
  /** 场景统一居中点（米，由全楼构件包围盒中心计算） */
  center: [number, number]
  /** 该层真实层高（米） */
  storyHeight: number
}

/** 收集构件平面坐标范围（跨楼层聚合用），无坐标返回 null */
export function elementsBounds(
  elements: SceneFloorElements,
): { minX: number; maxX: number; minY: number; maxY: number } | null {
  const xs: number[] = []
  const ys: number[] = []
  const push = (points: number[][] | undefined) => {
    for (const p of points ?? []) {
      if (p.length >= 2) {
        xs.push(p[0])
        ys.push(p[1])
      }
    }
  }
  for (const c of elements.columns) push(c.outline)
  for (const w of elements.walls) push(w.path)
  for (const b of elements.beams) push(b.path)
  for (const s of elements.slabs) push(s.outline)
  for (const p of elements.pipes) push(p.path)
  for (const e of elements.equipment) push(e.outline)
  if (xs.length < 2) return null
  return {
    minX: Math.min(...xs), maxX: Math.max(...xs),
    minY: Math.min(...ys), maxY: Math.max(...ys),
  }
}

/** 真实模式：仅居中平移，不缩放（1 three 单位 = 1 米） */
function realTransform(center: [number, number]): PlanTransform {
  return { toX: (x) => x - center[0], toZ: (y) => y - center[1], scale: 1 }
}

/** 归一化模式（旧 V2 行为，兜底）：构件等比压缩进 20×14 板片 */
function normalizedTransform(elements: SceneFloorElements): PlanTransform | null {
  const bounds = elementsBounds(elements)
  if (!bounds) return null
  const spanX = Math.max(bounds.maxX - bounds.minX, 1)
  const spanY = Math.max(bounds.maxY - bounds.minY, 1)
  const scale = Math.min(
    (FLOOR_WIDTH * PLAN_MARGIN) / spanX,
    (FLOOR_DEPTH * PLAN_MARGIN) / spanY,
  )
  return {
    toX: (x) => (x - bounds.minX - spanX / 2) * scale,
    toZ: (y) => (y - bounds.minY - spanY / 2) * scale,
    scale,
  }
}

function outlineShape(outline: number[][], t: PlanTransform): THREE.Shape | null {
  if (outline.length < 3) return null
  const shape = new THREE.Shape()
  shape.moveTo(t.toX(outline[0][0]), t.toZ(outline[0][1]))
  for (let i = 1; i < outline.length; i += 1) {
    shape.lineTo(t.toX(outline[i][0]), t.toZ(outline[i][1]))
  }
  shape.closePath()
  return shape
}

/** Shape 挤出并转为 y-up 放置（挤出方向默认 +z → 旋转为竖直） */
function extrudeUp(shape: THREE.Shape, height: number, baseY: number): THREE.BufferGeometry {
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false })
  geometry.rotateX(-Math.PI / 2)
  geometry.translate(0, baseY, 0)
  return geometry
}

/** 两点间箱体（墙/梁通用） */
function segmentBox(
  a: number[], b: number[], t: PlanTransform,
  width: number, height: number, centerY: number,
): THREE.BufferGeometry | null {
  const x0 = t.toX(a[0])
  const z0 = t.toZ(a[1])
  const x1 = t.toX(b[0])
  const z1 = t.toZ(b[1])
  const length = Math.hypot(x1 - x0, z1 - z0)
  if (length < 0.01) return null
  const geometry = new THREE.BoxGeometry(length, height, Math.max(width * t.scale, 0.05))
  geometry.rotateY(-Math.atan2(z1 - z0, x1 - x0))
  geometry.translate((x0 + x1) / 2, centerY, (z0 + z1) / 2)
  return geometry
}

function mergedMesh(
  geometries: THREE.BufferGeometry[],
  color: string,
  data: ElementUserData,
  opacity = 1,
): THREE.Mesh | null {
  if (!geometries.length) return null
  const merged = geometries.length === 1 ? geometries[0] : mergeGeometries(geometries)
  if (!merged) return null
  if (geometries.length > 1) geometries.forEach((g) => g.dispose())
  const material = new THREE.MeshLambertMaterial({
    color,
    transparent: opacity < 1,
    opacity,
  })
  const mesh = new THREE.Mesh(merged, material)
  mesh.userData = data
  return mesh
}

/**
 * 设备合批网格：整层设备挤出几何合并为一个 Mesh（1799→1 draw call/geometry），
 * 同时记录逐设备三角形区间以支持点击 faceIndex 反查该设备的 label/来源图纸。
 */
function buildEquipmentMergedMesh(
  items: ElementEquipment[], t: PlanTransform, floorY: number,
  floorKey: string, buildingKey: string,
): THREE.Mesh | null {
  const geometries: THREE.BufferGeometry[] = []
  const picks: EquipmentPick[] = []
  let faceAcc = 0
  for (const item of items) {
    const shape = outlineShape(item.outline, t)
    if (!shape) continue
    const height = Math.max(item.height * t.scale, 0.4)
    const geometry = extrudeUp(shape, height, floorY)
    const position = geometry.getAttribute('position')
    faceAcc += position ? position.count / 3 : 0
    picks.push({ faceEnd: faceAcc, label: item.label, src: item.src })
    geometries.push(geometry)
  }
  if (!geometries.length) return null
  const merged = geometries.length === 1 ? geometries[0] : mergeGeometries(geometries)
  if (!merged) return null
  if (geometries.length > 1) geometries.forEach((g) => g.dispose())
  const mesh = new THREE.Mesh(
    merged,
    new THREE.MeshLambertMaterial({ color: ELEMENT_COLORS.equipment }),
  )
  const data: ElementUserData = {
    kind: 'element', elementType: 'equipment', floorKey, buildingKey,
    count: picks.length, equipmentPicks: picks,
  }
  mesh.userData = data
  return mesh
}

/** 估算三角形数（粗略：几何 position 顶点数/3 累加） */
function triangleCount(meshes: THREE.Mesh[]): number {
  let total = 0
  for (const mesh of meshes) {
    const position = mesh.geometry.getAttribute('position')
    if (position) total += position.count / 3
  }
  return total
}

/**
 * 构建单楼层构件网格集合。
 * 返回 null = 该层无可渲染构件（调用方回退贴图）；超三角形预算同样返回 null。
 */
export function buildFloorElementMeshes(
  elements: SceneFloorElements,
  floorY: number,
  floorKey: string,
  buildingKey: string,
  real?: RealPlanOptions,
): THREE.Mesh[] | null {
  const t = real ? realTransform(real.center) : normalizedTransform(elements)
  if (!t) return null
  const baseY = floorY
  const storyH = real?.storyHeight ?? ELEMENT_STORY_HEIGHT
  const meshes: THREE.Mesh[] = []
  const meta = (
    elementType: string,
    items: { src?: string; source?: string; type_label?: string; type_text?: string }[],
  ): ElementUserData => ({
    kind: 'element', elementType, floorKey, buildingKey, count: items.length,
    ...collectSourceInfo(items),
  })

  const columnGeoms: THREE.BufferGeometry[] = []
  for (const column of elements.columns) {
    const shape = outlineShape(column.outline, t)
    if (shape) columnGeoms.push(extrudeUp(shape, storyH, baseY))
  }
  const columns = mergedMesh(columnGeoms, ELEMENT_COLORS.columns, meta('columns', elements.columns))
  if (columns) meshes.push(columns)

  const wallGeoms: THREE.BufferGeometry[] = []
  for (const wall of elements.walls) {
    for (let i = 0; i < wall.path.length - 1; i += 1) {
      const box = segmentBox(wall.path[i], wall.path[i + 1], t, wall.width, storyH, baseY + storyH / 2)
      if (box) wallGeoms.push(box)
    }
  }
  const walls = mergedMesh(wallGeoms, ELEMENT_COLORS.walls, meta('walls', elements.walls))
  if (walls) meshes.push(walls)

  const beamGeoms: THREE.BufferGeometry[] = []
  for (const beam of elements.beams) {
    for (let i = 0; i < beam.path.length - 1; i += 1) {
      const depth = Math.max(beam.depth * t.scale, 0.15)
      const box = segmentBox(
        beam.path[i], beam.path[i + 1], t, beam.width,
        depth, baseY + storyH - BEAM_TOP_OFFSET - depth / 2,
      )
      if (box) beamGeoms.push(box)
    }
  }
  const beams = mergedMesh(beamGeoms, ELEMENT_COLORS.beams, meta('beams', elements.beams))
  if (beams) meshes.push(beams)

  const slabGeoms: THREE.BufferGeometry[] = []
  for (const slab of elements.slabs) {
    const shape = outlineShape(slab.outline, t)
    if (shape) slabGeoms.push(extrudeUp(shape, SLAB_RENDER_THICKNESS, baseY - SLAB_RENDER_THICKNESS))
  }
  const slabs = mergedMesh(slabGeoms, ELEMENT_COLORS.slabs, meta('slabs', elements.slabs), 0.5)
  if (slabs) meshes.push(slabs)

  meshes.push(...buildPipeMeshes(elements, t, baseY, floorKey, buildingKey))

  const equipmentMesh = buildEquipmentMergedMesh(elements.equipment, t, baseY, floorKey, buildingKey)
  if (equipmentMesh) meshes.push(equipmentMesh)

  if (!meshes.length) return null
  if (triangleCount(meshes) > ELEMENT_TRIANGLE_BUDGET) {
    // 超三角形预算：整层降级贴图（调用方处理），先释放已建资源
    meshes.forEach((mesh) => {
      mesh.geometry.dispose()
      ;(mesh.material as THREE.Material).dispose()
    })
    return null
  }
  return meshes
}

const SHELL_COLOR = '#7ec1ff'
const SHELL_OPACITY = 0.16

/**
 * 建筑外观壳体：楼层板外轮廓垂直放样至层顶（半透明幕墙质感）。
 * 无 slab 轮廓时用构件包围盒；返回 null 表示该层无外壳依据。
 */
export function buildFloorShell(
  elements: SceneFloorElements,
  floorY: number,
  storyHeight: number,
  center: [number, number],
  floorKey: string,
  buildingKey: string,
): THREE.Mesh | null {
  const t = realTransform(center)
  let outline: number[][] | null = elements.slabs[0]?.outline ?? null
  if (!outline || outline.length < 3) {
    const bounds = elementsBounds(elements)
    if (!bounds) return null
    outline = [
      [bounds.minX, bounds.minY], [bounds.maxX, bounds.minY],
      [bounds.maxX, bounds.maxY], [bounds.minX, bounds.maxY],
    ]
  }
  const shape = outlineShape(outline, t)
  if (!shape) return null
  const material = new THREE.MeshLambertMaterial({
    color: SHELL_COLOR,
    transparent: true,
    opacity: SHELL_OPACITY,
    side: THREE.DoubleSide,
    depthWrite: false,
  })
  const mesh = new THREE.Mesh(extrudeUp(shape, storyHeight, floorY), material)
  const data: ElementUserData = {
    kind: 'element', elementType: 'shell', floorKey, buildingKey, count: 1,
  }
  mesh.userData = data
  return mesh
}


function buildPipeMeshes(
  elements: SceneFloorElements, t: PlanTransform, baseY: number,
  floorKey: string, buildingKey: string,
): THREE.Mesh[] {
  const bySystem = new Map<string, THREE.BufferGeometry[]>()
  for (const pipe of elements.pipes) {
    if (pipe.path.length < 2) continue
    const points = pipe.path.map(
      (p) => new THREE.Vector3(t.toX(p[0]), baseY + 2.2, t.toZ(p[1])),
    )
    const curve = new THREE.CatmullRomCurve3(points)
    const geometry = new THREE.TubeGeometry(curve, Math.max(points.length * 2, 4), PIPE_RENDER_RADIUS, 6, false)
    const list = bySystem.get(pipe.system) ?? []
    list.push(geometry)
    bySystem.set(pipe.system, list)
  }
  const meshes: THREE.Mesh[] = []
  bySystem.forEach((geometries, system) => {
    const mesh = mergedMesh(
      geometries,
      PIPE_SYSTEM_COLORS[system] ?? PIPE_SYSTEM_COLORS.其他,
      {
        kind: 'element', elementType: `pipes:${system}`,
        floorKey, buildingKey, count: geometries.length,
      },
    )
    if (mesh) meshes.push(mesh)
  })
  return meshes
}

// ── E2 轴网层 ────────────────────────────────────────────────────

const AXIS_LINE_COLOR = 0x8c8c8c
const AXIS_LABEL_SIZE_M = 2.4        // 轴号标签牌尺寸（米）
const AXIS_EXTEND_M = 3              // 轴线越出构件包络的出头长度（米）
const AXIS_LABEL_CANVAS_PX = 64

/** 轴号 → CanvasTexture 缓存：轴号跨楼层大量重复（"1"/"A"…），共享纹理防内存膨胀 */
const axisLabelTextureCache = new Map<string, THREE.CanvasTexture>()

function axisLabelTexture(label: string): THREE.CanvasTexture | null {
  const cached = axisLabelTextureCache.get(label)
  if (cached) return cached
  const canvas = document.createElement('canvas')
  canvas.width = AXIS_LABEL_CANVAS_PX
  canvas.height = AXIS_LABEL_CANVAS_PX
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  const center = AXIS_LABEL_CANVAS_PX / 2
  ctx.fillStyle = '#ffffff'
  ctx.strokeStyle = '#595959'
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.arc(center, center, center - 4, 0, Math.PI * 2)
  ctx.fill()
  ctx.stroke()
  ctx.fillStyle = '#262626'
  ctx.font = `bold ${label.length > 2 ? 22 : 30}px sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(label, center, center + 1)
  const texture = new THREE.CanvasTexture(canvas)
  axisLabelTextureCache.set(label, texture)
  return texture
}

function axisLabelSprite(label: string, position: THREE.Vector3): THREE.Sprite | null {
  const texture = axisLabelTexture(label)
  if (!texture) return null
  const material = new THREE.SpriteMaterial({ map: texture, depthTest: false })
  const sprite = new THREE.Sprite(material)
  sprite.position.copy(position)
  sprite.scale.set(AXIS_LABEL_SIZE_M, AXIS_LABEL_SIZE_M, 1)
  return sprite
}

/**
 * 楼层轴网（E2）：识别出的轴线 + 轴号标签，整层一个 Group
 * （elementType='axes'，与其他构件图层共用 elementFilter 显隐机制；
 * raycast 非递归遍历不命中 Group——轴网不可拾取，纯参照显示）。
 *
 * 仅真实坐标模式渲染（axes 坐标与构件同为米坐标系）。
 */
export function buildFloorAxes(
  axes: SceneFloorAxes,
  floorY: number,
  floorKey: string,
  buildingKey: string,
  real: RealPlanOptions,
  extent: { minX: number; maxX: number; minY: number; maxY: number },
): THREE.Group | null {
  if (!axes.x.length && !axes.y.length) return null
  const t = realTransform(real.center)
  const y = floorY + 0.05 // 略抬避免与楼板 z-fighting
  const positions: number[] = []
  const group = new THREE.Group()

  const zMin = t.toZ(extent.minY) - AXIS_EXTEND_M
  const zMax = t.toZ(extent.maxY) + AXIS_EXTEND_M
  for (const axis of axes.x) {
    const x = t.toX(axis.coord)
    positions.push(x, y, zMin, x, y, zMax)
    const sprite = axisLabelSprite(axis.label, new THREE.Vector3(x, y, zMin - 1))
    if (sprite) group.add(sprite)
  }
  const xMin = t.toX(extent.minX) - AXIS_EXTEND_M
  const xMax = t.toX(extent.maxX) + AXIS_EXTEND_M
  for (const axis of axes.y) {
    const z = t.toZ(axis.coord)
    positions.push(xMin, y, z, xMax, y, z)
    const sprite = axisLabelSprite(axis.label, new THREE.Vector3(xMin - 1, y, z))
    if (sprite) group.add(sprite)
  }

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  const lines = new THREE.LineSegments(
    geometry,
    new THREE.LineBasicMaterial({ color: AXIS_LINE_COLOR, transparent: true, opacity: 0.65 }),
  )
  group.add(lines)

  const data: ElementUserData = {
    kind: 'element', elementType: 'axes', floorKey, buildingKey,
    count: axes.x.length + axes.y.length,
  }
  group.userData = data
  return group
}
