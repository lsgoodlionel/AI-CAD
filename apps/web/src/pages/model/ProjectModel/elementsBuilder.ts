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

export interface ElementUserData {
  kind: 'element'
  /** columns | walls | beams | slabs | equipment | pipes:<system> */
  elementType: string
  floorKey: string
  buildingKey: string
  count: number
  label?: string
  src?: string
}

interface PlanTransform {
  toX: (x: number) => number
  toZ: (y: number) => number
  scale: number
}

/** 楼层构件包围盒 → 板片空间等比映射（无有效范围返回 null） */
function planTransform(elements: SceneFloorElements): PlanTransform | null {
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
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const spanX = Math.max(maxX - minX, 1)
  const spanY = Math.max(maxY - minY, 1)
  const scale = Math.min(
    (FLOOR_WIDTH * PLAN_MARGIN) / spanX,
    (FLOOR_DEPTH * PLAN_MARGIN) / spanY,
  )
  return {
    toX: (x) => (x - minX - spanX / 2) * scale,
    toZ: (y) => (y - minY - spanY / 2) * scale,
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

function buildEquipmentMesh(
  item: ElementEquipment, t: PlanTransform, floorY: number,
  floorKey: string, buildingKey: string,
): THREE.Mesh | null {
  const shape = outlineShape(item.outline, t)
  if (!shape) return null
  const height = Math.max(item.height * t.scale, 0.4)
  const mesh = new THREE.Mesh(
    extrudeUp(shape, height, floorY),
    new THREE.MeshLambertMaterial({ color: ELEMENT_COLORS.equipment }),
  )
  const data: ElementUserData = {
    kind: 'element', elementType: 'equipment', floorKey, buildingKey,
    count: 1, label: item.label, src: item.src,
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
): THREE.Mesh[] | null {
  const t = planTransform(elements)
  if (!t) return null
  const baseY = floorY
  const storyH = ELEMENT_STORY_HEIGHT
  const meshes: THREE.Mesh[] = []
  const meta = (elementType: string, count: number): ElementUserData => ({
    kind: 'element', elementType, floorKey, buildingKey, count,
  })

  const columnGeoms: THREE.BufferGeometry[] = []
  for (const column of elements.columns) {
    const shape = outlineShape(column.outline, t)
    if (shape) columnGeoms.push(extrudeUp(shape, storyH, baseY))
  }
  const columns = mergedMesh(columnGeoms, ELEMENT_COLORS.columns, meta('columns', elements.columns.length))
  if (columns) meshes.push(columns)

  const wallGeoms: THREE.BufferGeometry[] = []
  for (const wall of elements.walls) {
    for (let i = 0; i < wall.path.length - 1; i += 1) {
      const box = segmentBox(wall.path[i], wall.path[i + 1], t, wall.width, storyH, baseY + storyH / 2)
      if (box) wallGeoms.push(box)
    }
  }
  const walls = mergedMesh(wallGeoms, ELEMENT_COLORS.walls, meta('walls', elements.walls.length))
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
  const beams = mergedMesh(beamGeoms, ELEMENT_COLORS.beams, meta('beams', elements.beams.length))
  if (beams) meshes.push(beams)

  const slabGeoms: THREE.BufferGeometry[] = []
  for (const slab of elements.slabs) {
    const shape = outlineShape(slab.outline, t)
    if (shape) slabGeoms.push(extrudeUp(shape, SLAB_RENDER_THICKNESS, baseY - SLAB_RENDER_THICKNESS))
  }
  const slabs = mergedMesh(slabGeoms, ELEMENT_COLORS.slabs, meta('slabs', elements.slabs.length), 0.5)
  if (slabs) meshes.push(slabs)

  meshes.push(...buildPipeMeshes(elements, t, baseY, floorKey, buildingKey))

  for (const item of elements.equipment) {
    const mesh = buildEquipmentMesh(item, t, baseY, floorKey, buildingKey)
    if (mesh) meshes.push(mesh)
  }

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
