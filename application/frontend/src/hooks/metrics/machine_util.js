/**
 * 机器利用率 Hook
 * 数据源：metrics point 里的 metrics.machine_utilization (0-1)
 * Canonical shape: { step, metrics:{ machine_utilization, ... }, metrics_reward }
 */
export default {
  id: 'machine_util',
  label: '机器利用率',
  unit: '%',
  series: (ctx) => {
    const metrics = ctx.metrics || []
    return metrics.map((p) => ({
      x: p.step,
      y: Math.round(((p.metrics?.machine_utilization ?? 0) * 100) * 100) / 100,
    }))
  },
}
