/**
 * AGV 综合指标 Hook（多 series）
 * 数据源：canonical metrics 里的 AGV 字段（与容器化 SkyEngine 对齐）
 *   - agv_loaded_utilization: AGV 载货行驶占比
 *   - agv_busy_utilization:   AGV 非空闲占比
 *
 * 多 series：每个字段一条线。容器化 / StaticFactory 同构。
 */
const AGV_FIELDS = [
  { key: 'agv_loaded_utilization', label: 'AGV 载货利用率', color: '#64b5ff', pct: true },
  { key: 'agv_busy_utilization', label: 'AGV 忙碌利用率', color: '#66d06a', pct: true },
  { key: 'agv_travel_time_total', label: 'AGV 总行驶时间', color: '#ffb450', pct: false },
  { key: 'agv_waiting_time_total', label: 'AGV 总等待时间', color: '#ff6464', pct: false },
]

export default {
  id: 'agv_stats',
  label: 'AGV 综合指标',
  unit: '',
  multiSeries: true,
  series: (ctx) => {
    const metrics = ctx.metrics || []
    return AGV_FIELDS.map((f) => {
      const data = metrics
        .map((p) => {
          const v = p.metrics?.[f.key]
          if (typeof v !== 'number') return null
          return { x: p.step, y: f.pct ? Math.round(v * 10000) / 100 : v }
        })
        .filter(Boolean)
      return { name: f.label, data, color: f.color }
    }).filter((s) => s.data.length > 0)
  },
}
