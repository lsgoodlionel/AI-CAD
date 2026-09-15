import { describe, expect, it } from 'vitest'
import { formatElementTotals } from '../elementTotals'

describe('formatElementTotals', () => {
  it('shows recognised slabs as a subset of slabs, not as pipes', () => {
    // 实测 v86：`slabs_recognised: 29` 此前被显示成「管线29」
    const text = formatElementTotals({ slabs: 434, slabs_recognised: 29, pipes: 33940 })
    expect(text).toBe('板434（图层识别 29） / 管线33940')
    expect(text).not.toContain('管线29')
  })

  it('shows an unknown kind by its own key instead of guessing a label', () => {
    expect(formatElementTotals({ doors: 3 })).toBe('doors3')
  })

  it('omits zero counts', () => {
    expect(formatElementTotals({ columns: 0, walls: 2 })).toBe('墙2')
  })
})
