import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import FragmentPropertyPanel, {
  formatPropertyValue,
  toPrimaryEntries,
  toPsetSections,
} from '../FragmentPropertyPanel'
import type { PickedFragmentItem } from '@/services/projectModel'

const WALL: PickedFragmentItem = {
  localId: 42,
  ifcType: 'IFCWALL',
  guid: '3aB9$kZ2P1QeF7wXyZ0000',
  name: 'Basic Wall:200mm',
  psets: {
    Pset_WallCommon: { FireRating: 'REI 120', IsExternal: true },
  },
}

describe('formatPropertyValue', () => {
  it('renders primitives and dashes for empty values', () => {
    expect(formatPropertyValue('REI 120')).toBe('REI 120')
    expect(formatPropertyValue(true)).toBe('true')
    expect(formatPropertyValue(120)).toBe('120')
    expect(formatPropertyValue(null)).toBe('—')
    expect(formatPropertyValue(undefined)).toBe('—')
    expect(formatPropertyValue('   ')).toBe('—')
  })

  it('JSON-stringifies object values', () => {
    expect(formatPropertyValue({ a: 1 })).toBe('{"a":1}')
  })
})

describe('toPrimaryEntries', () => {
  it('surfaces IFC type / GUID / name / localId', () => {
    const entries = toPrimaryEntries(WALL)
    expect(entries).toContainEqual({ label: 'IFC 类型', value: 'IFCWALL' })
    expect(entries).toContainEqual({ label: 'GUID', value: '3aB9$kZ2P1QeF7wXyZ0000' })
    expect(entries).toContainEqual({ label: '名称', value: 'Basic Wall:200mm' })
    expect(entries).toContainEqual({ label: 'localId', value: '42' })
  })

  it('degrades missing fields to placeholders', () => {
    const entries = toPrimaryEntries({ localId: null, ifcType: '' })
    expect(entries).toContainEqual({ label: 'IFC 类型', value: '未知' })
    expect(entries).toContainEqual({ label: 'GUID', value: '—' })
    expect(entries).toContainEqual({ label: 'localId', value: '—' })
  })
})

describe('toPsetSections', () => {
  it('flattens psets into displayable sections', () => {
    const sections = toPsetSections(WALL)
    expect(sections).toHaveLength(1)
    expect(sections[0].name).toBe('Pset_WallCommon')
    expect(sections[0].entries).toContainEqual({ label: 'FireRating', value: 'REI 120' })
    expect(sections[0].entries).toContainEqual({ label: 'IsExternal', value: 'true' })
  })

  it('returns an empty array when there are no psets', () => {
    expect(toPsetSections({ localId: 1, ifcType: 'IFCBEAM' })).toEqual([])
  })
})

describe('FragmentPropertyPanel render', () => {
  it('shows the empty state when no item is selected', () => {
    const html = renderToStaticMarkup(createElement(FragmentPropertyPanel, { item: null }))
    expect(html).toContain('fragment-property-empty')
    expect(html).toContain('点击三维构件查看 IFC 属性')
  })

  it('shows the loading state', () => {
    const html = renderToStaticMarkup(
      createElement(FragmentPropertyPanel, { item: null, loading: true }),
    )
    expect(html).toContain('fragment-property-loading')
  })

  it('renders IFC type, GUID, name and pset values for a selected item', () => {
    const html = renderToStaticMarkup(createElement(FragmentPropertyPanel, { item: WALL }))
    expect(html).toContain('fragment-property-panel')
    expect(html).toContain('IFCWALL')
    expect(html).toContain('3aB9$kZ2P1QeF7wXyZ0000')
    expect(html).toContain('Basic Wall:200mm')
    expect(html).toContain('Pset_WallCommon')
    expect(html).toContain('REI 120')
  })

  it('renders a no-pset hint when the item has no property sets', () => {
    const html = renderToStaticMarkup(
      createElement(FragmentPropertyPanel, { item: { localId: 1, ifcType: 'IFCBEAM' } }),
    )
    expect(html).toContain('无 Pset 属性')
  })
})
