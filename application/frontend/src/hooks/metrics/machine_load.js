/**
 * 各机器负载 Hook（多 series）
 * 数据源：frame.machines[].load（经 normalizeSnapshot 后是 dict {"M1":{...}}）
 *
 * 设计：
 * - StaticFactory frame.machines[].load 是 0-100 的百分比，保语义
 * - 容器化通路 frame.machines[] 无 load 字段（只有 queue_length），自然降级为空
 * - 与 canonical metrics 无关，纯 frame 派生
 */
const DEFAULT_COLORS = ['#64b5ff', '#66d06a', '#ffb450', '#ff6464', '#b264ff']

export default {
  id: 'machine_load',
  label: '各机器负载',
  unit: '',
  multiSeries: true,
  series: (ctx) => {
    const frames = ctx.frames || []
    if (frames.length === 0) return []

    // labels 来自最后一帧的 machines keys（顺序假设稳定）
    const lastMachines = frames[frames.length - 1]?.machines || {}
    const labels = Array.isArray(lastMachines)
      ? lastMachines.map((m) => m.id)
      : Object.keys(lastMachines)
    if (labels.length === 0) return []

    return labels.map((label, i) => {
      const data = frames
        .map((f, idx) => {
          const machines = f.machines || {}
          const m = Array.isArray(machines)
            ? machines.find((mm) => mm.id === label)
            : machines[label]
          if (!m) return null
          // load 优先（StaticFactory），次选 queue_length（容器化）
          const v = typeof m.load === 'number'
            ? m.load
            : (typeof m.queue_length === 'number' ? m.queue_length : null)
          return v != null ? { x: idx, y: v } : null
        })
        .filter(Boolean)
      return {
        name: label,
        data,
        color: DEFAULT_COLORS[i % DEFAULT_COLORS.length],
      }
    }).filter((s) => s.data.length > 0)
  },
}
