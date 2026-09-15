import { describe, expect, it } from 'vitest'
import type { ModelScene } from '@/services/projectModel'
import { defaultElementFilter, elementFilterOptions } from '../modes/elementFilterOptions'

// 管线/设备金标准 0%（pipes 0/58、0/16；equipment 0/16）—— 默认隐藏并标「未验证」
const scene = {
  floors: [{ elements: { pipes: [{ system: '给排水' }] }, axes: null }],
} as unknown as ModelScene

describe('element filter defaults', () => {
  it('labels unverified kinds so the user knows why they are off', () => {
    const labels = elementFilterOptions(scene, ['pipes', 'equipment']).map((o) => o.label)
    expect(labels).toContain('管线·给排水（未验证）')
    expect(labels).toContain('设备（未验证）')
    expect(labels).toContain('柱')
  })

  it('hides unverified kinds by default', () => {
    const options = elementFilterOptions(scene, ['pipes', 'equipment'])
    const values = defaultElementFilter(options, ['pipes', 'equipment'])
    expect(values).toContain('columns')
    expect(values).not.toContain('pipes:给排水')
    expect(values).not.toContain('equipment')
  })

  it('keeps everything when the server reports nothing unverified', () => {
    const options = elementFilterOptions(scene)
    expect(defaultElementFilter(options, [])).toEqual(options.map((o) => o.value))
  })
})
