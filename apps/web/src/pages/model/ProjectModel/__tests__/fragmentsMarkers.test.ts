import { describe, expect, it } from 'vitest'
import {
  alignMarkerToFragments,
  alignMarkersToFragments,
  buildFloorPlacements,
  type FragmentsFloorPlacement,
} from '../fragmentsMarkers'
import type { SceneMarker } from '@/services/projectModel'

function marker(overrides: Partial<SceneMarker> = {}): SceneMarker {
  return {
    id: 'mk1',
    type: 'issue',
    severity: 'major',
    floor_key: 'F1',
    x: 0.5,
    y: 0.5,
    title: 'demo',
    discipline_code: 'S',
    ref: { drawing_id: 'd1' },
    ...overrides,
  }
}

const placement: FragmentsFloorPlacement = {
  floorKey: 'F1',
  elevation: 3,
  planWidth: 20,
  planDepth: 14,
}

describe('alignMarkerToFragments', () => {
  it('maps a centered marker to the plan center at floor elevation', () => {
    const aligned = alignMarkerToFragments(marker({ x: 0.5, y: 0.5 }), placement)
    expect(aligned.position.x).toBe(0)
    expect(aligned.position.z).toBe(0)
    expect(aligned.position.y).toBeCloseTo(3.2) // elevation + default lift 0.2
  })

  it('maps corners symmetrically around the plan center', () => {
    const min = alignMarkerToFragments(marker({ x: 0, y: 0 }), placement)
    const max = alignMarkerToFragments(marker({ x: 1, y: 1 }), placement)
    expect(min.position.x).toBe(-10)
    expect(min.position.z).toBe(-7)
    expect(max.position.x).toBe(10)
    expect(max.position.z).toBe(7)
  })

  it('applies center offset and custom lift', () => {
    const aligned = alignMarkerToFragments(marker({ x: 0.5, y: 0.5 }), {
      ...placement,
      center: [100, 50],
      markerLift: 1,
    })
    expect(aligned.position.x).toBe(100)
    expect(aligned.position.z).toBe(50)
    expect(aligned.position.y).toBe(4)
  })

  it('preserves the original marker reference', () => {
    const source = marker()
    expect(alignMarkerToFragments(source, placement).marker).toBe(source)
  })
})

describe('alignMarkersToFragments', () => {
  it('aligns markers with a known floor and skips unknown floors', () => {
    const placements = new Map([['F1', placement]])
    const result = alignMarkersToFragments(
      [marker({ id: 'a', floor_key: 'F1' }), marker({ id: 'b', floor_key: 'F9' })],
      placements,
    )
    expect(result.aligned.map((a) => a.marker.id)).toEqual(['a'])
    expect(result.skipped.map((m) => m.id)).toEqual(['b'])
  })

  it('returns empty buckets for empty input', () => {
    const result = alignMarkersToFragments([], new Map())
    expect(result.aligned).toEqual([])
    expect(result.skipped).toEqual([])
  })
})

describe('buildFloorPlacements', () => {
  it('uses real elevation_m when present and stacks by default otherwise', () => {
    const placements = buildFloorPlacements({
      floors: [
        { key: 'F1', label: '1F', elevation: 0, order: 0, drawings: [] },
        { key: 'F2', label: '2F', elevation: 0, order: 1, drawings: [], elevation_m: 4 },
      ] as never,
    })
    expect(placements.get('F1')?.elevation).toBe(0)
    expect(placements.get('F2')?.elevation).toBe(4)
  })

  it('recurs default story height when elevation_m is missing', () => {
    const placements = buildFloorPlacements(
      {
        floors: [
          { key: 'F1', label: '1F', elevation: 0, order: 0, drawings: [] },
          { key: 'F2', label: '2F', elevation: 0, order: 1, drawings: [] },
        ] as never,
      },
      { storyHeight: 3 },
    )
    expect(placements.get('F1')?.elevation).toBe(0)
    expect(placements.get('F2')?.elevation).toBe(3)
  })

  it('honors plan size overrides', () => {
    const placements = buildFloorPlacements(
      { floors: [{ key: 'F1', label: '1F', elevation: 0, order: 0, drawings: [] }] as never },
      { planWidth: 40, planDepth: 30 },
    )
    expect(placements.get('F1')?.planWidth).toBe(40)
    expect(placements.get('F1')?.planDepth).toBe(30)
  })
})
