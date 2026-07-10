/**
 * A-18 · 渲染模式选择 / 回退纯逻辑（sceneBuilder.ts）
 *
 * 这些函数决定「有 frag_key 走 Fragments、否则回退挤出/贴图」——是模式切换 UI 的
 * 数据源（index.tsx viewModeOptions / pickDefaultViewMode）。纯函数，node 环境可测；
 * WebGL 渲染留给 A-20 E2E。
 *
 * 同时覆盖 disposeObjectTree（切换/卸载时的资源释放，用假对象树断言递归 dispose）。
 */
import { describe, expect, it, vi } from 'vitest'
import {
  disposeObjectTree,
  pickDefaultViewMode,
  readModelIfc,
} from '../sceneBuilder'
import type { ModelScene } from '@/services/projectModel'

/** 构造带可选 model_ifc 的最小 ModelScene（只填被读取字段）。 */
function scene(
  overrides: {
    schemaVersion?: 1 | 2
    modelIfc?: unknown
  } = {},
): ModelScene {
  const base = {
    schema_version: overrides.schemaVersion ?? 2,
    project: { id: 'p1', name: '演示项目' },
    floors: [],
    markers: [],
    cross_links: [],
  }
  const withIfc =
    'modelIfc' in overrides ? { ...base, model_ifc: overrides.modelIfc } : base
  return withIfc as unknown as ModelScene
}

describe('readModelIfc', () => {
  it('returns null when scene carries no model_ifc', () => {
    expect(readModelIfc(scene())).toBeNull()
  })

  it('returns null for a non-object model_ifc (legacy/garbled)', () => {
    expect(readModelIfc(scene({ modelIfc: 'nope' }))).toBeNull()
    expect(readModelIfc(scene({ modelIfc: null }))).toBeNull()
  })

  it('reads a full model_ifc payload', () => {
    const ifc = readModelIfc(
      scene({
        modelIfc: {
          ifc_key: 'projects/p1/model_ifc/tower-a.ifc',
          frag_key: 'projects/p1/model_ifc/tower-a.frag',
          build_mode: 'ifc',
          is_estimated: true,
          generated_at: '2026-07-10T00:00:00Z',
        },
      }),
    )
    expect(ifc).toEqual({
      ifc_key: 'projects/p1/model_ifc/tower-a.ifc',
      frag_key: 'projects/p1/model_ifc/tower-a.frag',
      build_mode: 'ifc',
      is_estimated: true,
      generated_at: '2026-07-10T00:00:00Z',
    })
  })

  it('nulls a blank/whitespace frag_key (conversion not produced)', () => {
    expect(readModelIfc(scene({ modelIfc: { ifc_key: 'x', frag_key: '' } }))?.frag_key).toBeNull()
    expect(readModelIfc(scene({ modelIfc: { ifc_key: 'x', frag_key: '   ' } }))?.frag_key).toBeNull()
  })

  it('defaults build_mode to texture for unknown values and is_estimated to false', () => {
    const ifc = readModelIfc(scene({ modelIfc: { ifc_key: 'x', build_mode: 'weird' } }))
    expect(ifc?.build_mode).toBe('texture')
    expect(ifc?.is_estimated).toBe(false)
    expect(ifc?.generated_at).toBeUndefined()
  })
})

describe('pickDefaultViewMode', () => {
  it('prefers ifc when a frag_key is present', () => {
    const mode = pickDefaultViewMode(
      scene({ modelIfc: { ifc_key: 'x', frag_key: 'projects/p1/a.frag' } }),
    )
    expect(mode).toBe('ifc')
  })

  it('falls back to mixed for a V2 scene without frag_key', () => {
    expect(pickDefaultViewMode(scene({ schemaVersion: 2 }))).toBe('mixed')
  })

  it('falls back to texture for a V1 scene', () => {
    expect(pickDefaultViewMode(scene({ schemaVersion: 1 }))).toBe('texture')
  })

  it('does not pick ifc when frag_key is blank even on V2', () => {
    expect(
      pickDefaultViewMode(scene({ schemaVersion: 2, modelIfc: { ifc_key: 'x', frag_key: '' } })),
    ).toBe('mixed')
  })
})

// ── 资源释放：切换/卸载 three.js 场景时递归 dispose ─────────────

interface DisposeSpies {
  geometry: ReturnType<typeof vi.fn>
  material: ReturnType<typeof vi.fn>
  map: ReturnType<typeof vi.fn>
}

/** 造一个可被 disposeObjectTree.traverse 遍历的假网格（含 material.map）。 */
function fakeMesh(spies: DisposeSpies, withMap: boolean) {
  return {
    geometry: { dispose: spies.geometry },
    material: {
      dispose: spies.material,
      map: withMap ? { dispose: spies.map } : null,
    },
  }
}

describe('disposeObjectTree', () => {
  it('disposes geometry, material and texture map across the tree', () => {
    const spies: DisposeSpies = { geometry: vi.fn(), material: vi.fn(), map: vi.fn() }
    const meshes = [fakeMesh(spies, true), fakeMesh(spies, false)]
    const tree = {
      traverse: (cb: (child: unknown) => void) => meshes.forEach(cb),
    }

    disposeObjectTree(tree as unknown as Parameters<typeof disposeObjectTree>[0])

    expect(spies.geometry).toHaveBeenCalledTimes(2)
    expect(spies.material).toHaveBeenCalledTimes(2)
    expect(spies.map).toHaveBeenCalledTimes(1) // 仅第一个网格有贴图
  })

  it('disposes each material when material is an array', () => {
    const spies: DisposeSpies = { geometry: vi.fn(), material: vi.fn(), map: vi.fn() }
    const mesh = {
      geometry: { dispose: spies.geometry },
      material: [
        { dispose: spies.material, map: null },
        { dispose: spies.material, map: { dispose: spies.map } },
      ],
    }
    const tree = { traverse: (cb: (child: unknown) => void) => cb(mesh) }

    disposeObjectTree(tree as unknown as Parameters<typeof disposeObjectTree>[0])

    expect(spies.material).toHaveBeenCalledTimes(2)
    expect(spies.map).toHaveBeenCalledTimes(1)
  })
})
