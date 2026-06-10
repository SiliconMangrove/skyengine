import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useMonitorStore = defineStore('monitor', () => {
    // ============ 1. 事件队列 (Event Queue) ============
    const eventQueue = ref([])
    const totalEventCount = ref(0)

    const MAX_EVENT_BUFFER = 50

    // ============ 2. 图表/指标 State ============
    const chartData = ref({
        machine: { labels: [], data: [] },
        agv: { labels: [], data: [] },
        job: { labels: [], data: [] }
    })

    const keyMetrics = ref({
        efficiency: { value: '--', type: 'info' },
        utilization: { value: '--', type: 'info' }
    })

    // ============ 3. 运行时数据累积 (RAG 上下文) ============
    const metricsTimeline = ref([])
    const MAX_METRICS_TIMELINE = 500

    const runId = ref(null)
    const runStartTime = ref(null)
    const runMeta = ref({})

    // ============ 动作 (Actions) ============

    /**
     * 推送事件入队 (Push)
     */
    function pushEvent(payload) {
        const { title, message = '', type = 'info', idx = 0 } = payload

        const newEvent = {
            id: Date.now() + Math.random(),
            timestamp: new Date(),
            idx,
            title,
            message,
            type
        }

        eventQueue.value.push(newEvent)
        totalEventCount.value++

        if (eventQueue.value.length > MAX_EVENT_BUFFER) {
            eventQueue.value.shift()
        }
    }

    /**
     * 推送指标更新 (Push) — 同时累积 timeline
     */
    function pushMetrics(data) {
        // 更新图表
        if (data.machine) chartData.value.machine = data.machine
        if (data.agv) chartData.value.agv = data.agv
        if (data.job) chartData.value.job = data.job

        // 更新卡片指标
        if (data.keyMetrics) {
            keyMetrics.value = { ...keyMetrics.value, ...data.keyMetrics }
        }

        // 累积到 timeline
        metricsTimeline.value.push({
            timestamp: Date.now(),
            efficiency: data.keyMetrics?.efficiency?.value,
            utilization: data.keyMetrics?.utilization?.value,
            machine: data.machine,
            agv: data.agv,
            job: data.job,
        })
        if (metricsTimeline.value.length > MAX_METRICS_TIMELINE) {
            metricsTimeline.value = metricsTimeline.value.slice(-MAX_METRICS_TIMELINE)
        }
    }

    /**
     * 开始新运行
     */
    function startRun(meta = {}) {
        runId.value = meta.runId || `run_${Date.now()}`
        runStartTime.value = new Date().toISOString()
        runMeta.value = meta
        metricsTimeline.value = []
    }

    /**
     * 构造 RAG 上下文 — 从已有数据生成
     */
    function buildRAGContext(currentState = null) {
        const recentMetrics = metricsTimeline.value.slice(-20)

        // metrics 统计
        const metricStats = {}
        if (recentMetrics.length > 0) {
            const effs = recentMetrics.map(m => m.efficiency).filter(v => v != null && v !== '--')
            const utils = recentMetrics.map(m => m.utilization).filter(v => v != null && v !== '--')
            if (effs.length > 0) {
                const numEffs = effs.map(Number)
                metricStats.efficiency = {
                    current: numEffs[numEffs.length - 1],
                    avg: +(numEffs.reduce((a, b) => a + b, 0) / numEffs.length).toFixed(2),
                    min: Math.min(...numEffs),
                    max: Math.max(...numEffs),
                }
            }
            if (utils.length > 0) {
                const numUtils = utils.map(Number)
                metricStats.utilization = {
                    current: numUtils[numUtils.length - 1],
                    avg: +(numUtils.reduce((a, b) => a + b, 0) / numUtils.length).toFixed(2),
                    min: Math.min(...numUtils),
                    max: Math.max(...numUtils),
                }
            }
        }

        // 事件统计
        const eventStats = {}
        eventQueue.value.forEach(e => {
            eventStats[e.type] = (eventStats[e.type] || 0) + 1
        })

        return {
            run: {
                id: runId.value,
                start_time: runStartTime.value,
                meta: runMeta.value,
                duration: runStartTime.value
                    ? `${((Date.now() - new Date(runStartTime.value).getTime()) / 1000).toFixed(0)}s`
                    : '0s',
            },
            metric_stats: metricStats,
            event_stats: eventStats,
            total_events: totalEventCount.value,
            recent_metrics: recentMetrics.slice(-10),
            recent_events: eventQueue.value.slice(-20),
            current_state: currentState,
        }
    }

    // 重置/清空 Monitor 状态
    function clear() {
        eventQueue.value = []
        metricsTimeline.value = []
        runId.value = null
        runStartTime.value = null
        runMeta.value = {}
    }

    return {
        // State
        events: eventQueue,
        totalEventCount,
        chartData,
        keyMetrics,
        // 新增 State
        metricsTimeline,
        runId,
        runStartTime,
        runMeta,

        // Actions
        pushEvent,
        pushMetrics,
        startRun,
        buildRAGContext,
        clear,
    }
})
