/**
 * 事件计数 Hook（累计）
 * 数据源：events 列表，按 step(idx) 累计
 * 多 series：按 event.level 分组（info/success/warning/error）
 *
 * Canonical event shape: { timestamp, idx, type(业务名), level, message }
 */
const LEVEL_COLOR = {
  info: '#64b5ff',
  success: '#66d06a',
  warning: '#ffb450',
  error: '#ff6464',
}
const UNKNOWN_COLOR = '#a0a0a0'

export default {
  id: 'event_timeline',
  label: '事件累计',
  unit: '',
  multiSeries: true,
  series: (ctx) => {
    const events = ctx.events || []
    // 桶：level → { step(idx) → count }
    const buckets = {}
    events.forEach((e) => {
      const level = e?.level || 'info'
      const step = e?.idx ?? e?.step ?? 0
      if (!buckets[level]) buckets[level] = {}
      buckets[level][step] = (buckets[level][step] || 0) + 1
    })

    return Object.entries(buckets).map(([level, stepMap]) => {
      const steps = Object.keys(stepMap).map(Number).sort((a, b) => a - b)
      let cum = 0
      const data = steps.map((s) => {
        cum += stepMap[s]
        return { x: s, y: cum }
      })
      return { name: level, data, color: LEVEL_COLOR[level] || UNKNOWN_COLOR }
    })
  },
}
