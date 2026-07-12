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
import { resolveEquipmentPick } from '../elementsBuilder'
import type { EquipmentPick } from '../elementsBuilder'
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
