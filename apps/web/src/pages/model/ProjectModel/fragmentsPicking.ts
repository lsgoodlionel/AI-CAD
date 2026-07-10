/**
 * Fragments 构件拾取（A-08，WS2）——纯工具模块。
 *
 * 设计要点（seam 契约）：
 * - **不静态 import @thatopen**：本文件用结构化最小接口描述 @thatopen/fragments 的
 *   拾取/属性读取表面，避免对尚未安装的依赖产生运行时/编译耦合。并行 agent 的
 *   `FragmentsScene.tsx` 完成 raycast 后，把命中的 localId + 模型对象喂给这里，
 *   由本模块归一化为 `PickedFragmentItem`（与 onItemSelected(item) 的 item 形状一致）。
 * - 归一化对 @thatopen `getItemsData` 的返回做**防御式**解析：属性既支持
 *   `{value: ...}` 包装，也支持裸值；Pset 走 IFC 关系 `IsDefinedBy`。
 *
 * 期望的 getItemsData 配置见 {@link DEFAULT_ITEMS_DATA_CONFIG}。
 */
import type {
  FragmentPsets,
  PickedFragmentItem,
} from '@/services/projectModel'
import type { SemanticTreeGroup, SemanticTreeNodeView } from './types'

// ── @thatopen/fragments 最小结构接口（import type 语义，运行时无依赖）──

/** getItemsData 里单个属性的常见形态：{value, type} 包装或裸值 */
export interface RawFragmentAttribute {
  value?: unknown
  type?: number | string
}

/** getItemsData 返回的单条构件数据（属性名 → 属性/关系） */
export type RawFragmentItemData = Record<string, unknown>

/** getItemsData 调用配置（属性 + IsDefinedBy 关系，用于取 Pset） */
export interface ItemsDataConfig {
  attributesDefault?: boolean
  relations?: Record<string, { attributes?: boolean; relations?: boolean }>
}

/** @thatopen FragmentsModel 的最小拾取表面 */
export interface FragmentsModelLike {
  modelId?: string
  id?: string
  getItemsData: (
    localIds: number[],
    config?: ItemsDataConfig,
  ) => Promise<RawFragmentItemData[]> | RawFragmentItemData[]
}

/** raycast 命中的最小形态（不同 @thatopen 版本字段略有差异，全部可选） */
export interface FragmentRaycastHit {
  localId?: number | null
  expressId?: number | null
  fragmentId?: string
  modelId?: string
  id?: string
  point?: { x: number; y: number; z: number }
}

// ── 常量 ─────────────────────────────────────────────────────

/**
 * 请求属性 + Pset 的默认配置。IFC Pset 挂在构件的 `IsDefinedBy` 关系上，
 * 需展开一层 relations 才能拿到 `HasProperties`。
 */
export const DEFAULT_ITEMS_DATA_CONFIG: ItemsDataConfig = {
  attributesDefault: true,
  relations: {
    IsDefinedBy: { attributes: true, relations: true },
  },
}

// IFC 关系里承载 Pset 的属性名（不同导出器可能同时出现）
const PSET_RELATION_KEYS = ['IsDefinedBy', 'psets', 'Psets', 'HasPropertySets']
// 一个 Pset 内承载属性数组的键
const PSET_PROPS_KEYS = ['HasProperties', 'properties', 'Properties']
// 单个属性里承载"值"的候选键（IFC NominalValue / 简化导出）
const PROP_VALUE_KEYS = ['NominalValue', 'value', 'Value']
// 名称候选键
const NAME_KEYS = ['Name', 'name', 'LongName']

// ── 属性解包 ─────────────────────────────────────────────────

/** 读取 `{value}` 包装或裸值；两者皆不存在时返回 undefined */
export function attrValue(raw: unknown): unknown {
  if (raw === null || raw === undefined) return undefined
  if (typeof raw === 'object' && 'value' in (raw as RawFragmentAttribute)) {
    return (raw as RawFragmentAttribute).value
  }
  return raw
}

function firstDefined(source: RawFragmentItemData, keys: string[]): unknown {
  for (const key of keys) {
    if (key in source) {
      const value = attrValue(source[key])
      if (value !== undefined && value !== null && value !== '') return value
    }
  }
  return undefined
}

function toStr(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return undefined
}

function toNum(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return null
}

// ── Pset 提取 ────────────────────────────────────────────────

function extractSinglePset(pset: RawFragmentItemData): [string, Record<string, unknown>] | null {
  const psetName = toStr(firstDefined(pset, NAME_KEYS)) ?? 'Pset'
  const propsRaw = PSET_PROPS_KEYS.map((key) => pset[key]).find(Array.isArray) as
    | RawFragmentItemData[]
    | undefined
  if (!propsRaw) return null

  const props: Record<string, unknown> = {}
  for (const prop of propsRaw) {
    if (!prop || typeof prop !== 'object') continue
    const propName = toStr(firstDefined(prop, NAME_KEYS))
    if (!propName) continue
    props[propName] = firstDefined(prop, PROP_VALUE_KEYS) ?? null
  }
  if (Object.keys(props).length === 0) return null
  return [psetName, props]
}

/** 从构件数据里提取属性集；无 Pset 时返回 undefined */
export function extractPsets(raw: RawFragmentItemData): FragmentPsets | undefined {
  const relationArray = PSET_RELATION_KEYS.map((key) => raw[key]).find(Array.isArray) as
    | RawFragmentItemData[]
    | undefined
  if (!relationArray) return undefined

  const result: FragmentPsets = {}
  for (const entry of relationArray) {
    if (!entry || typeof entry !== 'object') continue
    const parsed = extractSinglePset(entry as RawFragmentItemData)
    if (parsed) result[parsed[0]] = parsed[1]
  }
  return Object.keys(result).length > 0 ? result : undefined
}

// ── 归一化 ───────────────────────────────────────────────────

export interface NormalizeOptions {
  modelId?: string
  /** raycast 已知的 localId（getItemsData 数据缺该字段时兜底） */
  localId?: number | null
}

/**
 * 把 @thatopen `getItemsData` 的一条原始记录归一化为 `PickedFragmentItem`。
 * 纯函数，防御式解析，缺字段安全降级为空串/undefined。
 */
export function normalizeFragmentItemData(
  raw: RawFragmentItemData,
  options: NormalizeOptions = {},
): PickedFragmentItem {
  const localId =
    toNum(firstDefined(raw, ['_localId', 'localId'])) ??
    (options.localId ?? null)
  const expressId = toNum(firstDefined(raw, ['expressId', '_expressId']))
  const ifcType = toStr(firstDefined(raw, ['_category', 'category', 'type', 'ifcType'])) ?? ''
  const guid = toStr(firstDefined(raw, ['_guid', 'guid', 'GlobalId']))
  const name = toStr(firstDefined(raw, NAME_KEYS))
  const psets = extractPsets(raw)

  return {
    localId,
    expressId,
    ifcType: ifcType.toUpperCase(),
    guid,
    name,
    modelId: options.modelId,
    psets,
  }
}

/** 从 raycast 命中里取 localId（多字段兜底） */
export function localIdFromHit(hit: FragmentRaycastHit | null | undefined): number | null {
  if (!hit) return null
  return toNum(hit.localId) ?? toNum(hit.expressId)
}

/**
 * 完整拾取：给定模型 + localId，异步读取属性并归一化。
 * 供 FragmentsScene raycast 命中后调用，产出 onItemSelected(item) 的 item。
 * 读取失败（模型未就绪/id 不存在）返回 null，不抛。
 */
export async function resolvePickedItem(
  model: FragmentsModelLike,
  localId: number,
  config: ItemsDataConfig = DEFAULT_ITEMS_DATA_CONFIG,
): Promise<PickedFragmentItem | null> {
  try {
    const dataList = await model.getItemsData([localId], config)
    const raw = Array.isArray(dataList) ? dataList[0] : undefined
    if (!raw) return null
    return normalizeFragmentItemData(raw, {
      modelId: model.modelId ?? model.id,
      localId,
    })
  } catch {
    return null
  }
}

// ── 语义树 ↔ 拾取联动（Phase A：按名称就近匹配）─────────────────

function normalizeName(value: string): string {
  return value.replace(/\s+/g, '').toLowerCase()
}

/**
 * 为拾取到的构件在语义树中寻找最匹配节点（Phase A 楼层/名称级）。
 * 用于「三维高亮 → 语义树选中」联动；无匹配返回 null。
 * 匹配优先级：构件名精确 > 构件名包含节点名 > 节点名包含构件名。
 */
export function findSemanticNodeForItem(
  groups: SemanticTreeGroup[],
  item: PickedFragmentItem | null,
): SemanticTreeNodeView | null {
  if (!item?.name) return null
  const target = normalizeName(item.name)
  if (!target) return null

  const nodes = groups.flatMap((group) => group.nodes)
  let contains: SemanticTreeNodeView | null = null
  let containedBy: SemanticTreeNodeView | null = null

  for (const node of nodes) {
    const canonical = normalizeName(node.canonicalName)
    if (!canonical) continue
    if (canonical === target) return node
    if (!contains && target.includes(canonical)) contains = node
    if (!containedBy && canonical.includes(target)) containedBy = node
  }
  return contains ?? containedBy
}
