/**
 * Analysis Log Store — 模块③ 离线日志分析（纯前端 demo 版）
 *
 * 设计：
 * - 不依赖后端，全部数据驻留内存（页面刷新即失，足够演示）
 * - episode 结束时调用 finalizeFromStores()，从 factory + monitor store 快照一份 run
 * - 离线看板读 run 数据，跑 Hook 渲染
 *
 * Run 数据形态：
 * {
 *   id, factory_id, algorithm, started_at, finished_at,
 *   total_steps,
 *   frames:        [...],            // factory.historyBuffer 的深拷贝
 *   metricsTimeline: [...],          // monitor.metricsTimeline 的深拷贝
 *   events: [...],                  // monitor.eventQueue 的深拷贝
 *   summary: {                      // 派生统计
 *     completed_jobs, total_jobs, makespan, avg_utilization, ...
 *   }
 * }
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { API_ROUTES } from '@/utils/api'

let runIdSeq = 0
function genRunId() {
  runIdSeq += 1
  const ts = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const stamp =
    `${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}` +
    `_${pad(ts.getHours())}${pad(ts.getMinutes())}${pad(ts.getSeconds())}`
  return `run_${stamp}_${String(runIdSeq).padStart(3, '0')}`
}

function computeSummary(frames, metricsTimeline, events) {
  // Job 完成情况：取最后一帧的 jobs 字段
  const lastFrame = frames[frames.length - 1] || {}
  const jobs = lastFrame.jobs || []
  const completedJobs = jobs.filter((j) => j.is_completed).length
  const totalJobs = jobs.length

  // makespan：总步数
  const makespan = frames.length

  // 平均利用率（machine_utilization）
  const utils = metricsTimeline
    .map((p) => p.metrics?.machine_utilization)
    .filter((v) => typeof v === 'number')
  const avgUtilization =
    utils.length > 0 ? utils.reduce((a, b) => a + b, 0) / utils.length : null

  // 事件分类计数
  const eventCounts = events.reduce((acc, e) => {
    const t = e?.type || e?.event?.type || 'unknown'
    acc[t] = (acc[t] || 0) + 1
    return acc
  }, {})

  return {
    total_steps: makespan,
    completed_jobs: completedJobs,
    total_jobs: totalJobs,
    avg_utilization: avgUtilization,
    event_total: events.length,
    event_counts: eventCounts,
  }
}

export const useAnalysisLogStore = defineStore('analysisLog', () => {
  const runs = ref([])

  /**
   * 从 factory + monitor store 快照一份 run，存到列表
   * @param {object} factoryStore  useFactoryStore()
   * @param {object} monitorStore  useMonitorStore()
   * @param {object} meta          { factory_id?, algorithm? }
   */
  function finalizeFromStores(factoryStore, monitorStore, meta = {}) {
    // 深拷贝，避免后续 store.reset() 把 run 数据也清掉
    const frames = JSON.parse(JSON.stringify(factoryStore.historyBuffer || []))
    const metricsTimeline = JSON.parse(JSON.stringify(monitorStore.metricsTimeline || []))
    const events = JSON.parse(JSON.stringify(monitorStore.eventQueue || monitorStore.events || []))

    const summary = computeSummary(frames, metricsTimeline, events)

    const startedAt =
      frames[0]?.env_timeline ||
      frames[0]?.timestamp ||
      null
    const finishedAt =
      frames[frames.length - 1]?.env_timeline ||
      frames[frames.length - 1]?.timestamp ||
      null

    const run = {
      id: genRunId(),
      factory_id: meta.factory_id || factoryStore.selectedFactoryId || 'unknown',
      algorithm: meta.algorithm || null,
      created_at: new Date().toISOString(),
      started_at: startedAt,
      finished_at: finishedAt,
      total_steps: frames.length,
      frames,
      metricsTimeline,
      events,
      summary,
    }
    runs.value.unshift(run)
    return run
  }

  function getRunById(id) {
    return runs.value.find((r) => r.id === id) || null
  }

  function deleteRun(id) {
    const idx = runs.value.findIndex((r) => r.id === id)
    if (idx >= 0) runs.value.splice(idx, 1)
  }

  function clearAll() {
    runs.value = []
  }

  /**
   * 通过后端接口下载（StaticFactory 的 mock 版本）。
   * 后端会加 Content-Disposition: attachment 头，浏览器原生触发下载，
   * 比纯前端 Blob + a.download 在 iframe/嵌入场景下更可靠。
   * 后端不可用时（开发态没起 backend）回退到前端 Blob。
   */
  async function _downloadViaBackend(payload) {
    const base = import.meta.env.VITE_API_URL || '/api'
    const url = `${base}${API_ROUTES.ANALYSIS_EXPORT}`
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const blob = await resp.blob()
      // 优先从 Content-Disposition 取文件名，否则用 payload.id
      const cd = resp.headers.get('content-disposition') || ''
      let filename = `${payload.id || 'run'}.json`
      const m = /filename="?([^";]+)"?/.exec(cd)
      if (m) filename = m[1]
      _triggerBrowserDownload(blob, filename)
      return true
    } catch (e) {
      console.warn('[analysisLog] backend export failed, fallback to Blob:', e)
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      _triggerBrowserDownload(blob, `${payload.id || 'run'}.json`)
      return false
    }
  }

  function _triggerBrowserDownload(blob, filename) {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  /**
   * 导出已存档的 run —— 走后端下载接口（StaticFactory mock 版）
   * @param {string} id
   * @returns {Promise<boolean>}
   */
  async function exportRun(id) {
    const run = getRunById(id)
    if (!run) return false
    await _downloadViaBackend(run)
    return true
  }

  /**
   * 导出当前会话（不入库）—— 直接把 store 当前数据序列化下载
   * 用于 EventPanel 的 "导出当前日志" 按钮
   */
  async function exportLive(factoryStore, monitorStore, meta = {}) {
    const snapshot = {
      id: `live_${Date.now()}`,
      factory_id: meta.factory_id || factoryStore.selectedFactoryId || 'unknown',
      algorithm: meta.algorithm || null,
      created_at: new Date().toISOString(),
      frames: JSON.parse(JSON.stringify(factoryStore.historyBuffer || [])),
      metricsTimeline: JSON.parse(JSON.stringify(monitorStore.metricsTimeline || [])),
      events: JSON.parse(JSON.stringify(monitorStore.eventQueue || monitorStore.events || [])),
    }
    snapshot.summary = computeSummary(snapshot.frames, snapshot.metricsTimeline, snapshot.events)
    await _downloadViaBackend(snapshot)
    return snapshot
  }

  const totalRuns = computed(() => runs.value.length)

  return {
    runs,
    totalRuns,
    finalizeFromStores,
    getRunById,
    deleteRun,
    clearAll,
    exportRun,
    exportLive,
  }
})
