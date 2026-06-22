<template>
    <div class="event-panel">
        <div class="event-header">
            <div class="header-left">
                <h3>{{ title }}</h3>
                <span class="event-counter" v-if="monitorStore.totalEventCount > 0">
                    Total: {{ monitorStore.totalEventCount }}
                </span>
            </div>
            <div class="header-actions">
                <button
                    class="icon-btn export-btn"
                    title="导出当前日志 (JSON)"
                    @click="handleExportLive"
                >⬇</button>
                <button
                    class="icon-btn analysis-btn"
                    title="查看历史样本"
                    @click="goToAnalysis"
                >📊</button>
                <button @click="monitorStore.clear" class="clear-btn icon-btn" title="清空">×</button>
            </div>
        </div>

        <!-- 嵌入：历史分析样本（默认展开，显示全部） -->
        <div v-if="analysisLog.totalRuns > 0" class="recent-runs-wrap">
            <div class="recent-runs-head" @click="toggleRecent">
                <span class="recent-title">
                    📦 历史样本
                    <span class="recent-count">{{ analysisLog.totalRuns }}</span>
                </span>
                <span class="recent-toggle">{{ showRecent ? '▾' : '▸' }}</span>
            </div>
            <transition name="recent-slide">
                <div v-show="showRecent" class="recent-runs-list">
                    <div
                        v-for="run in analysisLog.runs"
                        :key="run.id"
                        class="recent-run-item"
                        @click="openRun(run.id)"
                    >
                        <div class="recent-run-row">
                            <span class="recent-run-id">{{ run.id }}</span>
                            <span class="recent-run-factory">{{ run.factory_id }}</span>
                        </div>
                        <div class="recent-run-meta">
                            <span>{{ run.summary.total_steps }} 步</span>
                            <span>{{ run.summary.completed_jobs }}/{{ run.summary.total_jobs }} jobs</span>
                            <span>{{ run.summary.event_total }} events</span>
                        </div>
                    </div>
                </div>
            </transition>
        </div>

        <div class="event-list" ref="eventListRef">
            <div v-if="monitorStore.events.length === 0" class="no-events">
                <span>暂无系统日志</span>
            </div>

            <transition-group name="event-list-anim" tag="div" v-else>
                <div v-for="event in monitorStore.events" :key="event.id" class="event-item"
                    :class="['event-' + event.type]">
                    <div class="event-meta">
                        <span class="event-time">{{ formatTime(event.timestamp) }}</span>
                        <span class="event-idx">#{{ event.idx }}</span>
                    </div>
                    <div class="event-content">
                        <span class="event-icon">{{ getEventIcon(event.type) }}</span>
                        <div class="event-text">
                            <div class="event-title">{{ event.title }}</div>
                            <div v-if="event.message" class="event-message">{{ event.message }}</div>
                        </div>
                    </div>
                </div>
            </transition-group>
        </div>
    </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useMonitorStore } from '@/stores/monitor'
import { useAnalysisLogStore } from '@/stores/analysisLog'
import { useFactoryStore } from '@/stores/factory'
import { ElMessage } from 'element-plus'

defineProps({
    title: { type: String, default: '📋 系统事件' }
})

const router = useRouter()
const monitorStore = useMonitorStore()
const analysisLog = useAnalysisLogStore()
const factoryStore = useFactoryStore()
const eventListRef = ref(null)
// 默认展开历史样本列表；用户手动收起后保持收起
const showRecent = ref(true)

function toggleRecent() {
    showRecent.value = !showRecent.value
}

function goToAnalysis() {
    router.push('/analysis')
}

function openRun(id) {
    router.push(`/analysis/${id}`)
}

// 导出当前会话日志（不入库，直接序列化当前 store 数据）
async function handleExportLive() {
    try {
        await analysisLog.exportLive(factoryStore, monitorStore, {
            factory_id: factoryStore.selectedFactoryId,
        })
        ElMessage.success('已导出当前日志')
    } catch (e) {
        console.error('[EventPanel] exportLive failed:', e)
        ElMessage.error('导出失败')
    }
}

// 工具函数保持不变...
const eventTypeMap = { success: '✅', warning: '⚠️', error: '❌', info: 'ℹ️', task: '📌', agv: '🚛', machine: '⚙️' }
function getEventIcon(type) { return eventTypeMap[type] || 'ℹ️' }
function formatTime(ts) { return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false }) }

// 监听 Store 变化实现自动滚动
watch(
    () => monitorStore.events.length,
    () => {
        nextTick(() => {
            if (eventListRef.value) {
                eventListRef.value.scrollTo({
                    top: eventListRef.value.scrollHeight,
                    behavior: 'smooth'
                })
            }
        })
    }
)
</script>
<style scoped>
@import './styles/EventPanel.css';
</style>
