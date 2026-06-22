<template>
  <div class="analysis-detail-page">
    <!-- 顶部：返回 + run 元信息 -->
    <div class="page-header">
      <div class="header-left">
        <button class="back-btn" @click="$router.push('/analysis')">← 返回列表</button>
        <button class="back-btn factory-btn" @click="goBackToFactory">返回工厂 →</button>
        <h1 v-if="run" class="run-title" :title="run.id">📦 {{ run.id }}</h1>
        <h1 v-else>未找到样本</h1>
      </div>
      <div v-if="run" class="header-tags">
        <span class="tag factory-tag" :title="run.factory_id">{{ run.factory_id }}</span>
        <span v-if="run.algorithm" class="tag algo-tag" :title="run.algorithm">{{ run.algorithm }}</span>
        <span class="tag time-tag">{{ formatTime(run.created_at) }}</span>
      </div>
    </div>

    <!-- 不存在 -->
    <div v-if="!run" class="empty-state">
      <div class="empty-icon">📭</div>
      <p>该样本不存在或已被删除</p>
      <button class="entry-btn" @click="$router.push('/analysis')">返回列表 →</button>
    </div>

    <template v-else>
      <!-- 汇总卡片 -->
      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">总步数</div>
          <div class="summary-value">{{ run.summary.total_steps }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">Job 完成</div>
          <div class="summary-value">
            {{ run.summary.completed_jobs }}/{{ run.summary.total_jobs }}
          </div>
        </div>
        <div class="summary-card">
          <div class="summary-label">事件总数</div>
          <div class="summary-value">{{ run.summary.event_total }}</div>
        </div>
        <div class="summary-card" v-if="run.summary.avg_utilization != null">
          <div class="summary-label">平均利用率</div>
          <div class="summary-value">
            {{ (run.summary.avg_utilization * 100).toFixed(1) }}%
          </div>
        </div>
      </div>

      <!-- Hook 图表区 -->
      <div class="charts-grid">
        <div
          v-for="hook in hooks"
          :key="hook.id"
          class="chart-card"
        >
          <div class="chart-card-head">
            <span class="chart-title">{{ hook.label }}</span>
            <span v-if="hook.unit" class="chart-unit">unit: {{ hook.unit }}</span>
          </div>
          <LineChart
            :series="safeSeries(hook)"
            :y-label="hook.unit || ''"
            height="260px"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAnalysisLogStore } from '@/stores/analysisLog'
import { listHooks } from '@/hooks/metrics'
import LineChart from '@/components/charts/LineChart.vue'

const route = useRoute()
const router = useRouter()
const analysisLog = useAnalysisLogStore()
const hooks = listHooks()

// 同 AnalysisListView：显式带 query 参数回到上次的工厂
function goBackToFactory() {
  let lastFactory = null
  try { lastFactory = sessionStorage.getItem('skyengine_last_factory') } catch (e) {}
  if (lastFactory) {
    router.push({ path: '/factory', query: { factory: lastFactory } })
  } else {
    router.push('/factory')
  }
}

const run = computed(() => analysisLog.getRunById(route.params.runId))

// 归一化 ctx，复用同一套 hook（数据来自存档快照）
const ctx = computed(() => {
  if (!run.value) return { metrics: [], frames: [], events: [], source: 'archive' }
  return {
    metrics: run.value.metricsTimeline || [],
    frames: run.value.frames || [],
    events: run.value.events || [],
    source: 'archive',
  }
})

// 包一层 try/catch，单个 hook 报错不影响整页
function safeSeries(hook) {
  try {
    const r = hook.series(ctx.value)
    return Array.isArray(r) ? r : []
  } catch (e) {
    console.warn('[AnalysisDetail] hook error:', hook.id, e)
    return []
  }
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
</script>

<style scoped>
.analysis-detail-page {
  min-height: 100vh;
  background: linear-gradient(180deg, #0a1120 0%, #0d1426 100%);
  color: rgba(220, 230, 245, 0.9);
  padding: 24px 32px;
  font-family: 'Inter', 'PingFang SC', sans-serif;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(100, 180, 255, 0.1);
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
  flex: 1 1 auto;
}
.page-header h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: rgba(240, 245, 255, 1);
}
.run-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.back-btn {
  padding: 6px 12px;
  background: rgba(100, 180, 255, 0.08);
  border: 1px solid rgba(100, 180, 255, 0.2);
  border-radius: 6px;
  color: rgba(200, 220, 255, 0.8);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}
.back-btn:hover {
  background: rgba(100, 180, 255, 0.18);
  color: rgba(240, 245, 255, 1);
}
.back-btn.factory-btn {
  background: rgba(100, 180, 255, 0.15);
  border-color: rgba(100, 180, 255, 0.4);
  color: rgba(220, 230, 245, 0.95);
}
.header-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  flex-shrink: 0;
  max-width: 60%;
  justify-content: flex-end;
}
.tag {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 10px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.factory-tag {
  background: rgba(100, 180, 255, 0.12);
  color: rgba(180, 220, 255, 0.9);
}
.algo-tag {
  background: rgba(102, 208, 106, 0.12);
  color: rgba(150, 230, 160, 0.9);
}
.time-tag {
  background: rgba(255, 255, 255, 0.04);
  color: rgba(160, 190, 230, 0.6);
  font-variant-numeric: tabular-nums;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 80px 20px;
  text-align: center;
}
.empty-icon { font-size: 56px; opacity: 0.5; margin-bottom: 16px; }
.empty-state p {
  font-size: 13px;
  color: rgba(160, 190, 230, 0.5);
  margin: 0 0 20px;
}
.entry-btn {
  padding: 8px 20px;
  background: rgba(100, 180, 255, 0.15);
  border: 1px solid rgba(100, 180, 255, 0.35);
  border-radius: 6px;
  color: rgba(220, 230, 245, 0.95);
  font-size: 13px;
  cursor: pointer;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.summary-card {
  background: rgba(20, 26, 48, 0.6);
  border: 1px solid rgba(100, 180, 255, 0.15);
  border-radius: 10px;
  padding: 14px 16px;
}
.summary-label {
  font-size: 11px;
  color: rgba(160, 190, 230, 0.5);
  margin-bottom: 6px;
}
.summary-value {
  font-size: 22px;
  font-weight: 700;
  color: rgba(240, 245, 255, 0.95);
  font-variant-numeric: tabular-nums;
}

.charts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
  gap: 16px;
}
.chart-card {
  background: rgba(20, 26, 48, 0.5);
  border: 1px solid rgba(100, 180, 255, 0.12);
  border-radius: 10px;
  padding: 14px 16px;
}
.chart-card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8px;
}
.chart-title {
  font-size: 13px;
  font-weight: 600;
  color: rgba(220, 230, 245, 0.9);
}
.chart-unit {
  font-size: 10px;
  color: rgba(160, 190, 230, 0.4);
}
</style>
