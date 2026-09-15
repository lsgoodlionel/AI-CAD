/**
 * 模型页顶部「构件」统计的文字。
 *
 * `stats.elements_total` 里除了各类构件，还有 `slabs_recognised` ——
 * 它是**板的子集**（按图层识别出的真板，其余是兜底板），不是另一类构件。
 * 此前不认识的键一律显示成「管线」，于是页面上出现「管线29」，其实是 29 块识别板。
 */
const ELEMENT_TYPE_LABEL: Record<string, string> = {
  columns: '柱', walls: '墙', beams: '梁', slabs: '板', pipes: '管线', equipment: '设备',
}

const RECOGNISED_SLABS_KEY = 'slabs_recognised'

export function formatElementTotals(totals: Record<string, number>): string {
  const recognisedSlabs = totals[RECOGNISED_SLABS_KEY] ?? 0
  return Object.entries(totals)
    .filter(([kind, count]) => kind !== RECOGNISED_SLABS_KEY && count > 0)
    .map(([kind, count]) => {
      const label = ELEMENT_TYPE_LABEL[kind] ?? kind
      return kind === 'slabs' ? `${label}${count}（图层识别 ${recognisedSlabs}）` : `${label}${count}`
    })
    .join(' / ')
}
