import { describe, expect, it, vi } from 'vitest'
import {
  attrValue,
  DEFAULT_ITEMS_DATA_CONFIG,
  extractPsets,
  findSemanticNodeForItem,
  localIdFromHit,
  normalizeFragmentItemData,
  resolvePickedItem,
  type FragmentsModelLike,
  type RawFragmentItemData,
} from '../fragmentsPicking'
import type { SemanticTreeGroup } from '../types'

// 模拟 @thatopen/fragments getItemsData 的典型返回（{value} 包装 + IsDefinedBy 关系）
const WALL_RAW: RawFragmentItemData = {
  _localId: { value: 42 },
  _category: { value: 'IFCWALL' },
  _guid: { value: '3aB9$kZ2P1QeF7wXyZ0000' },
  Name: { value: 'Basic Wall:200mm' },
  IsDefinedBy: [
    {
      Name: { value: 'Pset_WallCommon' },
      HasProperties: [
        { Name: { value: 'FireRating' }, NominalValue: { value: 'REI 120' } },
        { Name: { value: 'IsExternal' }, NominalValue: { value: true } },
      ],
    },
  ],
}

describe('attrValue', () => {
  it('unwraps {value} attribute wrappers', () => {
    expect(attrValue({ value: 'IFCWALL' })).toBe('IFCWALL')
  })

  it('returns bare values untouched', () => {
    expect(attrValue('bare')).toBe('bare')
    expect(attrValue(7)).toBe(7)
  })

  it('returns undefined for null/undefined', () => {
    expect(attrValue(null)).toBeUndefined()
    expect(attrValue(undefined)).toBeUndefined()
  })
})

describe('normalizeFragmentItemData', () => {
  it('normalizes a wrapped @thatopen wall record into PickedFragmentItem', () => {
    const item = normalizeFragmentItemData(WALL_RAW, { modelId: 'm1' })

    expect(item.localId).toBe(42)
    expect(item.ifcType).toBe('IFCWALL')
    expect(item.guid).toBe('3aB9$kZ2P1QeF7wXyZ0000')
    expect(item.name).toBe('Basic Wall:200mm')
    expect(item.modelId).toBe('m1')
    expect(item.psets?.Pset_WallCommon?.FireRating).toBe('REI 120')
    expect(item.psets?.Pset_WallCommon?.IsExternal).toBe(true)
  })

  it('uppercases ifcType and falls back to empty string when missing', () => {
    const item = normalizeFragmentItemData({ _category: { value: 'ifccolumn' } })
    expect(item.ifcType).toBe('IFCCOLUMN')

    const bare = normalizeFragmentItemData({ Name: { value: 'x' } })
    expect(bare.ifcType).toBe('')
  })

  it('handles bare (non-wrapped) values and simplified pset keys', () => {
    const raw: RawFragmentItemData = {
      localId: 9,
      category: 'IFCDOOR',
      name: 'Door-1',
      psets: [
        {
          name: 'Pset_DoorCommon',
          properties: [{ name: 'FireRating', value: 'FD30' }],
        },
      ],
    }
    const item = normalizeFragmentItemData(raw)
    expect(item.localId).toBe(9)
    expect(item.ifcType).toBe('IFCDOOR')
    expect(item.name).toBe('Door-1')
    expect(item.psets?.Pset_DoorCommon?.FireRating).toBe('FD30')
  })

  it('falls back to the raycast localId when data omits it', () => {
    const item = normalizeFragmentItemData({ _category: { value: 'IFCSLAB' } }, { localId: 5 })
    expect(item.localId).toBe(5)
  })

  it('returns undefined psets when no property sets are present', () => {
    const item = normalizeFragmentItemData({ _category: { value: 'IFCBEAM' } })
    expect(item.psets).toBeUndefined()
  })
})

describe('extractPsets', () => {
  it('drops psets that carry no valid properties', () => {
    const psets = extractPsets({ IsDefinedBy: [{ Name: { value: 'Empty' }, HasProperties: [] }] })
    expect(psets).toBeUndefined()
  })
})

describe('localIdFromHit', () => {
  it('reads localId, falling back to expressId', () => {
    expect(localIdFromHit({ localId: 3 })).toBe(3)
    expect(localIdFromHit({ expressId: 8 })).toBe(8)
    expect(localIdFromHit(null)).toBeNull()
    expect(localIdFromHit({})).toBeNull()
  })
})

describe('resolvePickedItem', () => {
  it('calls model.getItemsData with the default pset config and normalizes', async () => {
    const model: FragmentsModelLike = {
      modelId: 'model-a',
      getItemsData: vi.fn().mockResolvedValue([WALL_RAW]),
    }
    const item = await resolvePickedItem(model, 42)

    expect(model.getItemsData).toHaveBeenCalledWith([42], DEFAULT_ITEMS_DATA_CONFIG)
    expect(item?.ifcType).toBe('IFCWALL')
    expect(item?.modelId).toBe('model-a')
  })

  it('returns null when the model yields no data', async () => {
    const model: FragmentsModelLike = { getItemsData: vi.fn().mockResolvedValue([]) }
    expect(await resolvePickedItem(model, 1)).toBeNull()
  })

  it('returns null (no throw) when getItemsData rejects', async () => {
    const model: FragmentsModelLike = {
      getItemsData: vi.fn().mockRejectedValue(new Error('model not ready')),
    }
    expect(await resolvePickedItem(model, 1)).toBeNull()
  })

  it('supports synchronous getItemsData implementations', async () => {
    const model: FragmentsModelLike = { getItemsData: () => [WALL_RAW] }
    const item = await resolvePickedItem(model, 42)
    expect(item?.name).toBe('Basic Wall:200mm')
  })
})

describe('findSemanticNodeForItem', () => {
  const groups: SemanticTreeGroup[] = [
    {
      type: 'building_unit',
      label: '单体',
      nodes: [
        {
          id: 'n1',
          title: '北区',
          canonicalName: '北区',
          normalizedKey: 'north',
          nodeType: 'building_unit',
          status: 'confirmed',
          confidence: 0.9,
          source: 'automatic',
          version: 1,
        },
      ],
    },
  ]

  it('matches an item whose name contains the node name', () => {
    const node = findSemanticNodeForItem(groups, {
      localId: 1,
      ifcType: 'IFCSPACE',
      name: '北区 3F 办公',
    })
    expect(node?.id).toBe('n1')
  })

  it('returns null when the item has no name or nothing matches', () => {
    expect(findSemanticNodeForItem(groups, { localId: 1, ifcType: 'IFCWALL' })).toBeNull()
    expect(
      findSemanticNodeForItem(groups, { localId: 1, ifcType: 'IFCWALL', name: '南区' }),
    ).toBeNull()
    expect(findSemanticNodeForItem(groups, null)).toBeNull()
  })
})
