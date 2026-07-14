/**
 * D-14 审校收件箱合并逻辑（review/reviewInbox.ts）纯函数单测。
 *
 * 覆盖：优先级公式与后端 routers/model_review.py `_priority()` 口径一致、
 * symbol/semantic 两类候选归一化、合并排序（冲突优先 → 低置信优先 → 稳定序）。
 */
import { describe, expect, it } from 'vitest'
import {
  computePriority,
  fromSemanticQueueRow,
  fromSymbolAnnotation,
  sortInboxItems,
  type ReviewInboxItem,
  type SemanticQueueRow,
} from '../review/reviewInbox'
import type { SymbolAnnotation } from '@/services/modelReview'

describe('computePriority', () => {
  it('gives conflicting items a 1000-point head start over any non-conflicting item', () => {
    const conflictLowPriority = computePriority(true, 0.99)
    const noConflictZeroConfidence = computePriority(false, 0)
    expect(conflictLowPriority).toBeGreaterThan(noConflictZeroConfidence)
  })

  it('ranks lower confidence higher within the same conflict tier', () => {
    expect(computePriority(false, 0.2)).toBeGreaterThan(computePriority(false, 0.8))
  })

  it('treats missing confidence as neutral 0.5, matching backend _UNKNOWN_CONFIDENCE', () => {
    expect(computePriority(false, undefined)).toBe(computePriority(false, 0.5))
  })
})

describe('fromSemanticQueueRow', () => {
  it('normalizes a semantic queue row into a ReviewInboxItem tagged kind=semantic', () => {
    const row: SemanticQueueRow = {
      id: 'wall-1', target_kind: 'topology', title: '墙未闭合',
      confidence: 0.3, conflict: true, drawing_id: 'd1',
    }
    const item = fromSemanticQueueRow(row)
    expect(item.kind).toBe('semantic')
    expect(item.conflict).toBe(true)
    expect(item.drawingId).toBe('d1')
    expect(item.priority).toBeGreaterThan(1000)
  })

  it('uses the backend-supplied priority when present instead of recomputing', () => {
    const row: SemanticQueueRow = {
      id: 'x', target_kind: 'naming', title: 'x', confidence: 0.9, conflict: false, priority: 42,
    }
    expect(fromSemanticQueueRow(row).priority).toBe(42)
  })
})

describe('fromSymbolAnnotation', () => {
  it('normalizes a symbol annotation into a ReviewInboxItem tagged kind=symbol', () => {
    const annotation: SymbolAnnotation = {
      id: 7, projectId: 'p1', drawingId: 'd1', category: 'column',
      bbox: [0, 0, 10, 10], confidence: 0.4, source: 'model', status: 'pending',
    }
    const item = fromSymbolAnnotation('d1', annotation)
    expect(item.kind).toBe('symbol')
    expect(item.drawingId).toBe('d1')
    expect(item.category).toBe('column')
    expect(item.conflict).toBe(false)
  })
})

describe('sortInboxItems', () => {
  function item(overrides: Partial<ReviewInboxItem>): ReviewInboxItem {
    return {
      key: 'k', kind: 'semantic', title: 't', confidence: 0.5, conflict: false,
      priority: 0, raw: { id: 'k', target_kind: 'element', title: 't' } as SemanticQueueRow,
      ...overrides,
    }
  }

  it('sorts conflicting items before any non-conflicting item regardless of source kind', () => {
    const conflictSymbol = item({ key: 'a', kind: 'symbol', conflict: true, priority: 1000 })
    const plainSemantic = item({ key: 'b', kind: 'semantic', conflict: false, priority: 10 })
    const sorted = sortInboxItems([plainSemantic, conflictSymbol])
    expect(sorted[0].key).toBe('a')
  })

  it('sorts by descending priority (low confidence first) within the same conflict tier', () => {
    const highConfidence = item({ key: 'a', priority: 20 })
    const lowConfidence = item({ key: 'b', priority: 80 })
    const sorted = sortInboxItems([highConfidence, lowConfidence])
    expect(sorted.map((i) => i.key)).toEqual(['b', 'a'])
  })

  it('breaks ties deterministically by key so re-renders do not reorder equal-priority rows', () => {
    const a = item({ key: 'a', priority: 50 })
    const b = item({ key: 'b', priority: 50 })
    expect(sortInboxItems([b, a]).map((i) => i.key)).toEqual(['a', 'b'])
  })
})
