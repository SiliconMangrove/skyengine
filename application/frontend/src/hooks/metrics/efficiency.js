/**
 * 系统效率 Hook
 * 数据源：metrics point 里的 metrics.efficiency (0-1)
 * Canonical shape: { step, metrics:{ efficiency, ... }, metrics_reward }
 */
export default {
  id: 'efficiency',
  label: '系统效率',
  unit: '%',
  series: (ctx) => {
    const metrics = ctx.metrics || []
    return metrics.map((p) => ({
      x: p.step,
      y: Math.round(((p.metrics?.efficiency ?? 0) * 100) * 100) / 100,
    }))
  },
}
