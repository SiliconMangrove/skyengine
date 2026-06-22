/**
 * Job 完成进度 Hook
 * 数据源：frames[*].jobs
 * 每步统计：已完成 Job 数 / 总 Job 数
 */
export default {
  id: 'job_progress',
  label: 'Job 完成进度',
  unit: '',
  multiSeries: true,
  series: (ctx) => {
    const frames = ctx.frames || []
    const data = frames.map((f, i) => {
      const jobs = f.jobs || []
      const done = jobs.filter((j) => j.is_completed).length
      return { x: i, y: done }
    })
    return [{ name: '已完成 Job', data, color: '#66d06a' }]
  },
}
