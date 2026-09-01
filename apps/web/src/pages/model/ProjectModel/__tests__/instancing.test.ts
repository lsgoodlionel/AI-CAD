/**
 * Task 1 · 内存优化：标记合批（InstancedMesh）+ 设备 faceIndex 拾取（纯几何逻辑）
 *
 * 覆盖 1500 标记 / 1799 设备从「逐个 Mesh」转为合批后的正确性：
 * - buildMarkerInstances：实例数、跳过缺失楼层、隐藏/还原矩阵
 * - resolveEquipmentPick：命中 faceIndex → 具体设备的 label/来源
 * three.js 在 node 环境下构造 InstancedMesh / SphereGeometry 不需要 WebGL。
 */
import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { applyMarkerVisibility, buildMarkerInstances } from '../sceneBuilder'
import { geometryTriangleCount, resolveEquipmentPick, resolveItemPick } from '../elementsBuilder'
import type { ElementItemPick, EquipmentPick } from '../elementsBuilder'
import type { SceneMarker } from '@/services/projectModel'

function marker(overrides: Partial<SceneMarker>): SceneMarker {
  return {
    id: 'm',
    floor_key: 'F1',
    x: 0.5,
    y: 0.5,
    severity: 'major',
    type: 'issue',
    title: 't',
    ...overrides,
  } as unknown as SceneMarker
}

/** 读取实例 i 的世界缩放（隐藏实例为 0）。 */
function instanceScale(mesh: THREE.InstancedMesh, i: number): number {
  const m = new THREE.Matrix4()
  mesh.getMatrixAt(i, m)
  return new THREE.Vector3().setFromMatrixScale(m).x
}

describe('buildMarkerInstances', () => {
  const floorY = new Map<string, number>([['F1', 0], ['F2', 4.5]])

  it('creates one instance per placeable marker and skips unknown floors', () => {
    const inst = buildMarkerInstances(
      [
        marker({ floor_key: 'F1' }),
        marker({ floor_key: 'F2' }),
        marker({ floor_key: 'GHOST' }), // 楼层缺失 → 跳过
      ],
      floorY,
    )
    expect(inst).not.toBeNull()
    expect(inst?.mesh.count).toBe(2)
    expect(inst?.markers).toHaveLength(2)
    expect(inst?.markers.map((m) => m.floor_key)).toEqual(['F1', 'F2'])
  })

  it('returns null when no marker is placeable', () => {
    expect(buildMarkerInstances([marker({ floor_key: 'GHOST' })], floorY)).toBeNull()
    expect(buildMarkerInstances([], floorY)).toBeNull()
  })

  it('hides filtered-out instances via zero-scale and restores them', () => {
    const inst = buildMarkerInstances(
      [marker({ severity: 'critical' }), marker({ severity: 'info' })],
      floorY,
    )!
    // 仅保留 critical
    applyMarkerVisibility(inst, (m) => m.severity === 'critical')
    expect(instanceScale(inst.mesh, 0)).toBeCloseTo(1)
    expect(instanceScale(inst.mesh, 1)).toBeCloseTo(0)
    // 全部还原
    applyMarkerVisibility(inst, () => true)
    expect(instanceScale(inst.mesh, 1)).toBeCloseTo(1)
  })
})

describe('resolveEquipmentPick', () => {
  const picks: EquipmentPick[] = [
    { faceEnd: 12, label: 'AHU-1', src: 'd1' },
    { faceEnd: 24, label: 'AHU-2', src: 'd2' },
    { faceEnd: 40, label: 'AHU-3', src: 'd3' },
  ]

  it('maps a face index to the equipment whose range contains it', () => {
    expect(resolveEquipmentPick(picks, 0)?.label).toBe('AHU-1')
    expect(resolveEquipmentPick(picks, 11)?.label).toBe('AHU-1')
    expect(resolveEquipmentPick(picks, 12)?.label).toBe('AHU-2') // faceEnd 为 exclusive
    expect(resolveEquipmentPick(picks, 39)?.label).toBe('AHU-3')
  })

  it('clamps an out-of-range face index to the last equipment', () => {
    expect(resolveEquipmentPick(picks, 999)?.label).toBe('AHU-3')
  })

  it('returns null for an empty pick list', () => {
    expect(resolveEquipmentPick([], 3)).toBeNull()
  })
})


// ── 逐构件反向追溯：三角形口径必须与 raycaster faceIndex 一致 ──────────

describe('geometryTriangleCount', () => {
  it('索引几何(BoxGeometry,墙/梁)按 index 计数,而非 position', () => {
    // Arrange：BoxGeometry 是索引几何 —— position 24 顶点,index 36 → 12 个三角形
    const box = new THREE.BoxGeometry(1, 1, 1)

    // Act
    const triangles = geometryTriangleCount(box)

    // Assert：raycaster 的 faceIndex 按 index 编号(0..11),故必须是 12
    expect(box.index).not.toBeNull()
    expect(box.getAttribute('position').count / 3).toBe(8) // 旧口径(错误)
    expect(triangles).toBe(12)                             // 新口径(与 faceIndex 一致)
  })

  it('非索引几何(ExtrudeGeometry,柱/板)按 position 计数', () => {
    // Arrange
    const shape = new THREE.Shape()
    shape.moveTo(0, 0); shape.lineTo(1, 0); shape.lineTo(1, 1); shape.closePath()
    const extruded = new THREE.ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false })

    // Act / Assert
    expect(extruded.index).toBeNull()
    expect(geometryTriangleCount(extruded))
      .toBe(extruded.getAttribute('position').count / 3)
  })
})

describe('resolveItemPick 边界与索引几何对齐', () => {
  it('墙/梁按 12 三角形/段累加时,faceIndex 落在正确构件上', () => {
    // Arrange：两段墙,各 12 个三角形(BoxGeometry 真实值)
    const picks: ElementItemPick[] = [
      { faceEnd: 12, src: 'drawing-A' },
      { faceEnd: 24, src: 'drawing-B' },
    ]

    // Act / Assert：第 11 面属第一段,第 12 面(exclusive)已属第二段
    expect(resolveItemPick(picks, 0)?.src).toBe('drawing-A')
    expect(resolveItemPick(picks, 11)?.src).toBe('drawing-A')
    expect(resolveItemPick(picks, 12)?.src).toBe('drawing-B')
    expect(resolveItemPick(picks, 23)?.src).toBe('drawing-B')
  })

  it('旧的 8 三角形/段口径会把第二段的面误判成第一段(回归保护)', () => {
    // 旧口径产出的错误边界
    const stalePicks: ElementItemPick[] = [
      { faceEnd: 8, src: 'drawing-A' },
      { faceEnd: 16, src: 'drawing-B' },
    ]
    // 真实第一段有 12 个面,第 10 面本应属 A,旧边界却判给 B —— 即追溯到错误图纸
    expect(resolveItemPick(stalePicks, 10)?.src).toBe('drawing-B')
  })
})
