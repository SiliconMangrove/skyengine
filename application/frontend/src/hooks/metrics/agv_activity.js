/**
 * AGV 活动度 Hook
 * 数据源：frames.grid_state.is_active (true 的 AGV 数)
 * 兼容在线离线：每帧 grid_state.is_active 数组里 true 的个数
 */
export default {
  id: 'agv_activity',
  label: 'AGV 活动数',
  unit: '',
  multiSeries: true,
  series: (ctx) => {
    const frames = ctx.frames || []
    const data = frames.map((f, i) => {
      const activeArr = f.grid_state?.is_active || []
      const activeCount = activeArr.filter(Boolean).length
      return { x: i, y: activeCount }
    })
    return [{ name: '活跃 AGV', data, color: '#64b5ff' }]
  },
}
