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

    // ============ 4. sim_server (DockerFactory) 专用 ============
    // episode 结束后的汇总 + 热力图
    const heatmaps = ref(null)
    const episodeSummary = ref(null)

    // ============ 5. 智能体对话历史 (跨组件持久) ============
    // 结构: { id, role: 'user'|'assistant', content, timestamp, templateId?, streaming?, error? }
    const agentHistory = ref([])
    const MAX_AGENT_HISTORY = 30
    // 最近选中的模板 id (用于按钮高亮持久)
    const agentActiveTemplate = ref(null)

    /** 追加一条 Agent 消息 */
    function pushAgentMessage(msg) {
        agentHistory.value.push({
            id: Date.now() + Math.random(),
            timestamp: new Date(),
            ...msg,
        })
        if (agentHistory.value.length > MAX_AGENT_HISTORY) {
            agentHistory.value = agentHistory.value.slice(-MAX_AGENT_HISTORY)
        }
    }

    /** 流式追加 chunk 到最后一条 assistant(没有则新建) */
    function appendAgentChunk(chunk) {
        const last = agentHistory.value[agentHistory.value.length - 1]
        if (last && last.role === 'assistant' && last.streaming) {
            last.content += chunk
        } else {
            pushAgentMessage({ role: 'assistant', content: chunk, streaming: true })
        }
    }

    /** 结束最后一条消息流式状态 */
    function finalizeAgentMessage() {
        const last = agentHistory.value[agentHistory.value.length - 1]
        if (last) last.streaming = false
    }

    /** 标记最后一条 assistant 失败 */
    function failAgentMessage(errMsg) {
        const last = agentHistory.value[agentHistory.value.length - 1]
        if (last && last.role === 'assistant') {
            last.streaming = false
            last.error = true
            if (!last.content) last.content = errMsg || '⚠ AI 服务暂不可用'
        }
    }

    /** 清空 Agent 对话历史 */
    function clearAgentHistory() {
        agentHistory.value = []
        agentActiveTemplate.value = null
    }

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
        heatmaps.value = null
        episodeSummary.value = null
    }

    /**
     * 清空 sim_server 专用状态 (DockerFactory 切换场景时调用)
     */
    function clearSim() {
        eventQueue.value = []
        metricsTimeline.value = []
        heatmaps.value = null
        episodeSummary.value = null
    }

    /**
     * 推送 sim_server (DockerFactory) 指标更新
     * - data.status === "running": 增量时序点 {step, metrics:{...}, metrics_reward}
     * - data.status === "stopped": episode 汇总 + 热力图
     */
    function pushSimMetrics(data) {
        if (!data) return
        if (data.status === 'stopped') {
            if (data.episode_summary) episodeSummary.value = data.episode_summary
            if (data.heatmap) heatmaps.value = data.heatmap
            pushEvent({
                title: '仿真完成',
                message: `makespan=${data.episode_summary?.completed_makespan ?? '--'}`,
                type: 'success',
                idx: data.step ?? 0,
            })
            return
        }
        if (data.status === 'running' && data.metrics) {
            metricsTimeline.value.push({
                step: data.step,
                timestamp: Date.now(),
                metrics: { ...data.metrics },
                metrics_reward: data.metrics_reward,
            })
            if (metricsTimeline.value.length > MAX_METRICS_TIMELINE) {
                metricsTimeline.value = metricsTimeline.value.slice(-MAX_METRICS_TIMELINE)
            }
        }
    }

    /**
     * 推送 sim_server (DockerFactory) 业务事件
     * data: {timestamp, type, message, level}
     */
    function pushSimEvent(data) {
        if (!data) return
        const typeMap = {
            success: 'success',
            info: 'info',
            warning: 'warning',
            error: 'error',
        }
        pushEvent({
            title: data.type || 'event',
            message: data.message || '',
            type: typeMap[data.level] || 'info',
            idx: 0,
        })
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
        // sim_server 专用
        heatmaps,
        episodeSummary,
        // Agent 对话
        agentHistory,
        agentActiveTemplate,

        // Actions
        pushEvent,
        pushMetrics,
        startRun,
        buildRAGContext,
        clear,
        // sim_server 专用
        clearSim,
        pushSimMetrics,
        pushSimEvent,
        // Agent 对话
        pushAgentMessage,
        appendAgentChunk,
        finalizeAgentMessage,
        failAgentMessage,
        clearAgentHistory,
    }
})
