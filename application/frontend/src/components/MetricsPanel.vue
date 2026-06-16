<template>
    <div class="metrics-panel">
        <div class="panel-header">
            <h3>📊 {{ title }}</h3>
        </div>

        <!-- ============ sim_server (DockerFactory) 模式 ============ -->
        <template v-if="isSimMode">
            <!-- 关键指标卡片 -->
            <div class="metrics-grid">
                <div v-for="metric in simMetricCards" :key="metric.key" class="metric-card"
                    :class="metric.type || 'default'">
                    <div class="metric-icon">{{ metric.icon }}</div>
                    <div class="metric-content">
                        <div class="metric-label">{{ metric.label }}</div>
                        <div class="metric-value">{{ metric.value }}</div>
                        <div v-if="metric.detail" class="metric-detail">{{ metric.detail }}</div>
                    </div>
                </div>
            </div>

            <!-- 时间序列折线图 -->
            <div class="chart-section" v-if="simTimeline.steps.length > 1">
                <h4>实时趋势 (随 step 变化)</h4>
                <div class="chart-item" @click="openSimBigChart()">
                    <div class="chart-info">
                        <span class="chart-title">机器利用率 / AGV 装载利用率</span>
                        <span class="chart-tag">Trend</span>
                    </div>
                    <div class="mini-chart-wrapper">
                        <v-chart class="chart-canvas" :option="simTimelineOption" autoresize />
                    </div>
                </div>
            </div>

            <!-- Episode 汇总卡片 -->
            <div class="chart-section" v-if="monitorStore.episodeSummary">
                <h4>🏁 Episode 汇总</h4>
                <div class="metrics-grid">
                    <div v-for="card in episodeCards" :key="card.key" class="metric-card success">
                        <div class="metric-icon">{{ card.icon }}</div>
                        <div class="metric-content">
                            <div class="metric-label">{{ card.label }}</div>
                            <div class="metric-value">{{ card.value }}</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 热力图 (occupancy) -->
            <div class="chart-section" v-if="monitorStore.heatmaps">
                <h4>🔥 占用率热力图</h4>
                <div class="heatmap-wrapper">
                    <div class="heatmap-grid" :style="heatmapGridStyle">
                        <div v-for="(cell, i) in heatmapOccupancyCells" :key="i"
                            class="heatmap-cell" :style="cell.style" :title="cell.title" />
                    </div>
                </div>
                <div class="heatmap-legend">
                    <span>低</span>
                    <div class="legend-bar"></div>
                    <span>高</span>
                </div>
            </div>
        </template>

        <!-- ============ PacketFactory 默认模式 ============ -->
        <template v-else>
            <div class="metrics-grid">
                <div v-for="metric in metricsArray" :key="metric.label" class="metric-card"
                    :class="metric.type || 'default'">
                    <div class="metric-icon">{{ metric.icon }}</div>
                    <div class="metric-content">
                        <div class="metric-label">{{ metric.label }}</div>
                        <div class="metric-value">{{ metric.value }}</div>
                        <div v-if="metric.detail" class="metric-detail">{{ metric.detail }}</div>
                    </div>
                </div>
            </div>

            <div class="chart-section" v-if="showChart">
                <h4>实时监控图表</h4>
                <div class="chart-list">

                    <div class="chart-item" @click="openBigChart('machine')">
                        <div class="chart-info">
                            <span class="chart-title">机器负载分布</span>
                            <span class="chart-tag">Load</span>
                        </div>
                        <div class="mini-chart-wrapper">
                            <v-chart class="chart-canvas" :option="miniMachineOption" autoresize />
                        </div>
                    </div>

                    <div class="chart-item" @click="openBigChart('agv')">
                        <div class="chart-info">
                            <span class="chart-title">AGV 任务统计</span>
                            <span class="chart-tag success">Transport</span>
                        </div>
                        <div class="mini-chart-wrapper">
                            <v-chart class="chart-canvas" :option="miniAgvOption" autoresize />
                        </div>
                    </div>

                    <div class="chart-item" @click="openBigChart('job')">
                        <div class="chart-info">
                            <span class="chart-title">任务延迟趋势</span>
                            <span class="chart-tag warning">Latency</span>
                        </div>
                        <div class="mini-chart-wrapper">
                            <v-chart class="chart-canvas" :option="miniJobOption" autoresize />
                        </div>
                    </div>
                </div>
            </div>
        </template>

        <slot></slot>

        <el-dialog v-model="dialogVisible" :title="currentBigChartTitle" width="60%" align-center append-to-body
            class="chart-dialog">
            <div class="big-chart-container">
                <v-chart class="big-chart" :option="currentBigChartOption" autoresize />
            </div>
        </el-dialog>
    </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useMonitorStore } from '@/stores/monitor'
// --- ECharts 引入 ---
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";
import {
    TitleComponent,
    TooltipComponent,
    LegendComponent,
    GridComponent,
} from "echarts/components";

use([
    CanvasRenderer,
    LineChart,
    BarChart,
    TitleComponent,
    TooltipComponent,
    LegendComponent,
    GridComponent,
]);

// --- Props ---
const props = defineProps({
    title: { type: String, default: '关键指标' },
    showChart: { type: Boolean, default: true },
    // 'grid_factory' / 'docker' 触发 sim 模式；否则走 PacketFactory 默认
    factoryType: { type: String, default: '' },
})

// --- Store ---
const monitorStore = useMonitorStore()

const isSimMode = computed(() =>
    props.factoryType === 'grid_factory' || props.factoryType === 'docker'
)

// ============ sim_server 模式: 关键指标卡片 ============
const SIM_METRICS_META = {
    machine_utilization: { label: '机器利用率', icon: '⚙️', unit: '%', color: 'info', pct: true },
    agv_loaded_utilization: { label: 'AGV 装载率', icon: '🚛', unit: '%', color: 'success', pct: true },
    agv_busy_utilization: { label: 'AGV 忙碌率', icon: '🤖', unit: '%', color: 'info', pct: true },
    transport_delay_ratio: { label: '运输延迟比', icon: '⏱️', unit: '%', color: 'warning', pct: true },
    swap_conflict_count: { label: '交换冲突', icon: '⚠️', color: 'warning' },
    transport_blocking_delay_mean: { label: '阻塞延迟', icon: '🚧', color: 'warning' },
    machine_load_variance: { label: '负载方差', icon: '📊', color: 'default' },
    operation_queue_waiting_time_mean: { label: '队列等待', icon: '📥', color: 'info' },
    agv_travel_time_total: { label: '行驶时间', icon: '🛣️', color: 'info' },
    agv_waiting_time_total: { label: 'AGV 等待', icon: '⌛', color: 'info' },
    machine_non_processing_time_mean: { label: '非加工时间', icon: '🛠️', color: 'default' },
    tasked_stationary_count: { label: '带任务静止', icon: '🅿️', color: 'warning' },
    machine_waiting_for_inbound_transfer_ratio: { label: '入站等待比', icon: '⏳', color: 'info', pct: true },
}

function formatValue(key, raw) {
    if (raw == null || Number.isNaN(raw)) return '--'
    const meta = SIM_METRICS_META[key]
    if (meta?.pct) return `${(raw * 100).toFixed(1)}${meta.unit || ''}`
    if (typeof raw === 'number') {
        return Number.isInteger(raw) ? `${raw}` : raw.toFixed(3)
    }
    return `${raw}`
}

const LAST_METRICS = computed(() => {
    const tl = monitorStore.metricsTimeline
    if (!tl || tl.length === 0) return null
    return tl[tl.length - 1].metrics || null
})

const simMetricCards = computed(() => {
    const m = LAST_METRICS.value
    if (!m) return []
    // 优先展示 6 个关键指标
    const pri = ['machine_utilization', 'agv_loaded_utilization', 'transport_delay_ratio',
        'swap_conflict_count', 'transport_blocking_delay_mean', 'machine_load_variance']
    return pri.map(key => {
        const meta = SIM_METRICS_META[key] || { label: key, icon: '📊' }
        return {
            key,
            label: meta.label,
            icon: meta.icon,
            value: formatValue(key, m[key]),
            type: meta.color,
        }
    })
})

// ============ sim_server 模式: 时间序列折线图 ============
const simTimeline = computed(() => {
    const tl = monitorStore.metricsTimeline || []
    const steps = tl.map(p => p.step ?? 0)
    const machineUtil = tl.map(p => (p.metrics?.machine_utilization ?? null) * 100)
    const agvLoaded = tl.map(p => (p.metrics?.agv_loaded_utilization ?? null) * 100)
    return { steps, machineUtil, agvLoaded }
})

const simTimelineOption = computed(() => {
    const t = simTimeline.value
    return {
        grid: { top: 20, bottom: 24, left: 40, right: 10 },
        legend: { data: ['机器利用率', 'AGV 装载率'], top: 0, textStyle: { fontSize: 10 } },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: t.steps, axisLabel: { fontSize: 10 } },
        yAxis: { type: 'value', max: 100, axisLabel: { fontSize: 10, formatter: '{value}%' } },
        series: [
            {
                name: '机器利用率',
                type: 'line',
                smooth: true,
                showSymbol: false,
                data: t.machineUtil,
                itemStyle: { color: '#667eea' },
                areaStyle: { opacity: 0.15 },
            },
            {
                name: 'AGV 装载率',
                type: 'line',
                smooth: true,
                showSymbol: false,
                data: t.agvLoaded,
                itemStyle: { color: '#4CAF50' },
                areaStyle: { opacity: 0.15 },
            },
        ],
    }
})

function openSimBigChart() {
    currentBigChartTitle.value = '利用率趋势'
    currentBigChartOption.value = {
        title: { text: '机器利用率 / AGV 装载率', left: 'center' },
        tooltip: { trigger: 'axis' },
        legend: { data: ['机器利用率', 'AGV 装载率'], top: 30 },
        grid: { top: 80, bottom: 40, left: 50, right: 30 },
        xAxis: { type: 'category', data: simTimeline.value.steps, name: 'step' },
        yAxis: { type: 'value', max: 100, name: '%' },
        series: [
            { name: '机器利用率', type: 'line', smooth: true, data: simTimeline.value.machineUtil, itemStyle: { color: '#667eea' } },
            { name: 'AGV 装载率', type: 'line', smooth: true, data: simTimeline.value.agvLoaded, itemStyle: { color: '#4CAF50' } },
        ],
    }
    dialogVisible.value = true
}

// ============ sim_server 模式: Episode 汇总 ============
const episodeCards = computed(() => {
    const s = monitorStore.episodeSummary
    if (!s) return []
    const items = [
        { key: 'completed_makespan', label: '完工 makespan', icon: '⏱️', value: s.completed_makespan },
        { key: 'full_makespan', label: '完整 makespan', icon: '🕒', value: s.full_makespan },
        { key: 'success_rate', label: '成功率', icon: '✅', value: formatPct(s.success_rate) },
        { key: 'job_completion_rate', label: '作业完成率', icon: '📦', value: formatPct(s.job_completion_rate) },
    ].filter(it => it.value != null)
    return items
})

function formatPct(v) {
    if (v == null) return null
    return `${(v * 100).toFixed(1)}%`
}

// ============ sim_server 模式: 热力图 ============
const heatmapGridStyle = computed(() => {
    const hm = monitorStore.heatmaps
    if (!hm || !hm.occupancy || !hm.occupancy.length) return {}
    const rows = hm.occupancy.length
    const cols = hm.occupancy[0]?.length || 0
    return {
        gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gridTemplateRows: `repeat(${rows}, 1fr)`,
        aspectRatio: `${cols} / ${rows}`,
    }
})

const heatmapOccupancyCells = computed(() => {
    const hm = monitorStore.heatmaps
    if (!hm || !hm.occupancy) return []
    const occ = hm.occupancy
    const obs = hm.obstacles || []
    const cells = []
    // 计算最大占用值用于归一化
    let maxV = 0
    for (let y = 0; y < occ.length; y++) {
        for (let x = 0; x < occ[y].length; x++) {
            if (occ[y][x] > maxV) maxV = occ[y][x]
        }
    }
    for (let y = 0; y < occ.length; y++) {
        for (let x = 0; x < occ[y].length; x++) {
            const isObstacle = obs[y] && obs[y][x]
            const v = occ[y][x]
            const norm = maxV > 0 ? v / maxV : 0
            const style = isObstacle
                ? { background: '#222' }
                : { background: `rgba(100, 180, 255, ${0.1 + norm * 0.9})` }
            cells.push({ style, title: `(${x},${y}) occ=${v}${isObstacle ? ' [障碍]' : ''}` })
        }
    }
    return cells
})

// ============ PacketFactory 默认模式 (保留原逻辑) ============
const metricsArray = computed(() => {
    if (!monitorStore.keyMetrics || Object.keys(monitorStore.keyMetrics).length === 0) {
        return []
    }

    const metricsMeta = {
        efficiency: { label: '生产效率', icon: '⚡', detail: '设备产出率' },
        utilization: { label: '设备利用率', icon: '🔧', detail: '设备运行占比' },
        avgLatency: { label: '平均延迟', icon: '⏱️', detail: '任务响应时间' },
        throughput: { label: '吞吐量', icon: '📈', detail: '单位时间产量' },
        agvCount: { label: 'AGV 数量', icon: '🤖', detail: '在线 AGV 台数' },
        machineHealth: { label: '设备健康度', icon: '❤️', detail: '无故障运行率' }
    }

    return Object.entries(monitorStore.keyMetrics).map(([key, metric]) => ({
        label: metricsMeta[key]?.label || key,
        icon: metricsMeta[key]?.icon || '📊',
        value: metric.value || '--',
        type: metric.type || 'default',
        detail: metricsMeta[key]?.detail || '',
        key: key
    }))
})

const createMiniOption = (type, data, color) => {
    const isLine = type === 'line'
    return {
        grid: { top: 5, bottom: 5, left: 5, right: 5 },
        xAxis: { type: 'category', show: false },
        yAxis: { type: 'value', show: false },
        series: [{
            data: data || [],
            type: type,
            smooth: true,
            showSymbol: false,
            itemStyle: { color: color },
            areaStyle: isLine ? { opacity: 0.2, color: color } : undefined,
            barWidth: '60%'
        }],
        animationDuration: 500
    }
}

const createBigOption = (type, data, labels, title, color) => {
    return {
        title: { text: title, left: 'center' },
        tooltip: { trigger: 'axis' },
        grid: { top: 50, bottom: 30, left: 50, right: 20 },
        xAxis: { type: 'category', data: labels || [] },
        yAxis: { type: 'value' },
        series: [{
            data: data || [],
            type: type,
            smooth: true,
            itemStyle: { color: color },
            barWidth: '40%',
            markLine: type === 'line' ? { data: [{ type: 'average', name: 'Avg' }] } : undefined
        }]
    }
}

const miniMachineOption = computed(() =>
    createMiniOption('bar', monitorStore.chartData.machine.data, '#667eea')
)
const miniAgvOption = computed(() =>
    createMiniOption('bar', monitorStore.chartData.agv.data, '#4CAF50')
)
const miniJobOption = computed(() =>
    createMiniOption('line', monitorStore.chartData.job.data, '#FF9800')
)

const dialogVisible = ref(false)
const currentBigChartTitle = ref('')
const currentBigChartOption = ref({})

const openBigChart = (type) => {
    const chartData = monitorStore.chartData[type] || { data: [], labels: [] }

    if (type === 'machine') {
        currentBigChartTitle.value = '机器负载详情'
        currentBigChartOption.value = createBigOption('bar', chartData.data, chartData.labels, 'Machine Load', '#667eea')
    } else if (type === 'agv') {
        currentBigChartTitle.value = 'AGV 运输统计'
        currentBigChartOption.value = createBigOption('bar', chartData.data, chartData.labels, 'AGV Load', '#4CAF50')
    } else if (type === 'job') {
        currentBigChartTitle.value = '任务处理延迟趋势'
        currentBigChartOption.value = createBigOption('line', chartData.data, chartData.labels, 'Job Latency (ms)', '#FF9800')
    }
    dialogVisible.value = true
}
</script>

<style scoped>
@import './styles/MetricsPanel.css';

/* sim 模式专属样式 */
.heatmap-wrapper {
    margin-top: 8px;
    background: rgba(0, 0, 0, 0.2);
    border-radius: 6px;
    padding: 8px;
}

.heatmap-grid {
    display: grid;
    gap: 1px;
    max-width: 100%;
    max-height: 240px;
}

.heatmap-cell {
    min-width: 4px;
    min-height: 4px;
    border-radius: 1px;
}

.heatmap-legend {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    margin-top: 6px;
    font-size: 11px;
    color: #a0a0a0;
}

.heatmap-legend .legend-bar {
    width: 120px;
    height: 8px;
    background: linear-gradient(to right,
        rgba(100, 180, 255, 0.1),
        rgba(100, 180, 255, 1));
    border-radius: 4px;
}

.metrics-panel h4 {
    margin: 12px 0 6px;
    font-size: 13px;
    color: #c0d0e0;
}
</style>
