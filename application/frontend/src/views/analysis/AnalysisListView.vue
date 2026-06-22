<template>
  <div class="analysis-list-page">
    <div class="page-header">
      <div class="header-left">
        <button class="back-btn" @click="goBackToFactory">← 返回工厂</button>
        <h1>📋 离线分析样本</h1>
      </div>
      <div class="header-right">
        <span class="count-tag">共 {{ analysisLog.totalRuns }} 条</span>
        <button v-if="analysisLog.totalRuns > 0" class="clear-btn" @click="handleClearAll">
          清空全部
        </button>
      </div>
    </div>

    <div v-if="analysisLog.totalRuns === 0" class="empty-state">
      <div class="empty-icon">📊</div>
      <h2>暂无分析样本</h2>
      <p>请到工厂页面跑一次仿真，episode 结束后会自动保存到此处</p>
      <button class="entry-btn" @click="goBackToFactory">前往工厂 →</button>
    </div>

    <div v-else class="run-list">
      <div
        v-for="run in analysisLog.runs"
        :key="run.id"
        class="run-card"
        @click="$router.push(`/analysis/${run.id}`)"
      >
        <div class="run-card-head">
          <span class="run-id" :title="run.id">📦 {{ run.id }}</span>
          <span class="run-factory-tag" :title="run.factory_id">{{ run.factory_id }}</span>
          <span v-if="run.algorithm" class="run-algo-tag" :title="run.algorithm">{{ run.algorithm }}</span>
          <button
            class="run-export-btn"
            @click.stop="handleExport(run.id)"
            title="导出 JSON"
          >⬇</button>
          <button
            class="run-delete-btn"
            @click.stop="handleDelete(run.id)"
            title="删除"
          >×</button>
        </div>

        <div class="run-meta-row">
          <div class="meta-item">
            <span class="meta-label">总步数</span>
            <span class="meta-value">{{ run.total_steps }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Job 完成</span>
            <span class="meta-value">
              {{ run.summary.completed_jobs }}/{{ run.summary.total_jobs }}
            </span>
          </div>
          <div class="meta-item">
            <span class="meta-label">事件总数</span>
            <span class="meta-value">{{ run.summary.event_total }}</span>
          </div>
          <div class="meta-item" v-if="run.summary.avg_utilization != null">
            <span class="meta-label">平均利用率</span>
            <span class="meta-value">
              {{ (run.summary.avg_utilization * 100).toFixed(1) }}%
            </span>
          </div>
        </div>

        <div class="run-footer">
          <span class="created-at">{{ formatTime(run.created_at) }}</span>
          <span class="view-link">查看详情 →</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useAnalysisLogStore } from '@/stores/analysisLog'
import { ElMessageBox, ElMessage } from 'element-plus'

const router = useRouter()
const analysisLog = useAnalysisLogStore()

// 显式带 query 参数回到上次的工厂（FactoryView 会读 query.factory 自动重新进入）
// 不用 router.back() —— 在 list ↔ detail 之间会乱跳
function goBackToFactory() {
  let lastFactory = null
  try { lastFactory = sessionStorage.getItem('skyengine_last_factory') } catch (e) {}
  if (lastFactory) {
    router.push({ path: '/factory', query: { factory: lastFactory } })
  } else {
    router.push('/factory')
  }
}

function formatTime(iso) {
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function handleDelete(id) {
  ElMessageBox.confirm('确定删除这条样本？', '删除确认', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
    .then(() => {
      analysisLog.deleteRun(id)
      ElMessage.success('已删除')
    })
    .catch(() => {})
}

async function handleExport(id) {
  const ok = await analysisLog.exportRun(id)
  if (ok) ElMessage.success('已导出')
  else ElMessage.error('样本不存在')
}

function handleClearAll() {
  ElMessageBox.confirm('确定清空全部样本？此操作不可恢复', '清空确认', {
    type: 'warning',
    confirmButtonText: '清空',
    cancelButtonText: '取消',
  })
    .then(() => {
      analysisLog.clearAll()
      ElMessage.success('已清空')
    })
    .catch(() => {})
}
</script>

<style scoped>
.analysis-list-page {
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
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(100, 180, 255, 0.1);
  margin-bottom: 24px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.page-header h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: rgba(240, 245, 255, 1);
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
}
.back-btn:hover {
  background: rgba(100, 180, 255, 0.18);
  color: rgba(240, 245, 255, 1);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.count-tag {
  font-size: 12px;
  color: rgba(160, 190, 230, 0.6);
  padding: 4px 10px;
  background: rgba(100, 180, 255, 0.06);
  border-radius: 10px;
}
.clear-btn {
  padding: 4px 10px;
  background: rgba(255, 100, 100, 0.08);
  border: 1px solid rgba(255, 100, 100, 0.25);
  border-radius: 6px;
  color: rgba(255, 150, 150, 0.9);
  font-size: 12px;
  cursor: pointer;
}
.clear-btn:hover { background: rgba(255, 100, 100, 0.18); }

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  text-align: center;
}
.empty-icon { font-size: 56px; opacity: 0.5; margin-bottom: 16px; }
.empty-state h2 {
  font-size: 18px;
  margin: 0 0 8px;
  color: rgba(220, 230, 245, 0.8);
}
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
.entry-btn:hover { background: rgba(100, 180, 255, 0.25); }

.run-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 16px;
}
.run-card {
  background: rgba(20, 26, 48, 0.6);
  border: 1px solid rgba(100, 180, 255, 0.15);
  border-radius: 10px;
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.2s;
}
.run-card:hover {
  border-color: rgba(100, 180, 255, 0.4);
  background: rgba(20, 26, 48, 0.85);
  transform: translateY(-1px);
}
.run-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  min-width: 0;
}
.run-id {
  font-size: 14px;
  font-weight: 700;
  color: rgba(240, 245, 255, 1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
  max-width: 45%;
}
.run-factory-tag, .run-algo-tag {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 8px;
  background: rgba(100, 180, 255, 0.12);
  color: rgba(180, 220, 255, 0.9);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.run-factory-tag { max-width: 30%; }
.run-algo-tag {
  background: rgba(102, 208, 106, 0.12);
  color: rgba(150, 230, 160, 0.9);
  max-width: 30%;
}
.run-export-btn {
  margin-left: auto;
  background: none;
  border: none;
  color: rgba(160, 190, 230, 0.4);
  font-size: 13px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}
.run-export-btn:hover { color: rgba(100, 180, 255, 0.9); }
.run-delete-btn {
  background: none;
  border: none;
  color: rgba(160, 190, 230, 0.4);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}
.run-delete-btn:hover { color: rgba(255, 100, 100, 0.9); }

.run-meta-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  padding: 10px 0;
  border-top: 1px dashed rgba(100, 180, 255, 0.1);
  border-bottom: 1px dashed rgba(100, 180, 255, 0.1);
  margin-bottom: 10px;
}
.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.meta-label {
  font-size: 10px;
  color: rgba(160, 190, 230, 0.5);
}
.meta-value {
  font-size: 16px;
  font-weight: 700;
  color: rgba(240, 245, 255, 0.95);
  font-variant-numeric: tabular-nums;
}

.run-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
}
.created-at {
  color: rgba(160, 190, 230, 0.45);
  font-variant-numeric: tabular-nums;
}
.view-link {
  color: rgba(100, 180, 255, 0.7);
  font-size: 11px;
}
</style>
