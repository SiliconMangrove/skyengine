/**
 * 事件计数 Hook（累计）
 * 数据源：events 列表，按 step 累计
 * 多 series：按 event.type 分组（success/error/info/task）
 */
export default {
  id: 'event_timeline',
  label: '事件累计',
  unit: '',
  multiSeries: true,
  series: (ctx) => {
    const events = ctx.events || []
    const typeBuckets = {} // type → { step → count }
    events.forEach((e) => {
      const t = e?.type || e?.event?.type || 'unknown'
      const step = e?.step ?? e?.event?.step ?? 0
      if (!typeBuckets[t]) typeBuckets[t] = {}
      typeBuckets[t][step] = (typeBuckets[t][step] || 0) + 1
    })

    // 每个 type 一条累计折线
    const colorMap = {
      success: '#66d06a',
      error: '#ff6464',
      info: '#64b5ff',
      task: '#ffb450',
      unknown: '#a0a0a0',
    }
    return Object.entries(typeBuckets).map(([type, stepMap]) => {
      const steps = Object.keys(stepMap)
        .map(Number)
        .sort((a, b) => a - b)
      let cum = 0
      const data = steps.map((s) => {
        cum += stepMap[s]
        return { x: s, y: cum }
      })
      return { name: type, data, color: colorMap[type] || colorMap.unknown }
    })
  },
}
