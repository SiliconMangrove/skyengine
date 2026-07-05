<template>
  <div class="job-insert-panel">
    <!-- 上传区 -->
    <div
      class="drop-zone"
      :class="{ active: isDragging }"
      @click="triggerFileInput"
      @dragover.prevent="isDragging = true"
      @dragleave.prevent="isDragging = false"
      @drop.prevent="handleDrop"
    >
      <input
        ref="fileInputRef"
        type="file"
        accept=".json,application/json"
        style="display: none"
        @change="handleFileSelect"
      />
      <div class="drop-content">
        <span class="drop-icon">📂</span>
        <p class="drop-title">拖拽 FJSP 文件到此处</p>
        <p class="drop-hint">或点击选择 .json（如 dataset/fjsp/J10P5M6.json）</p>
      </div>
    </div>

    <!-- 解析结果 -->
    <template v-if="parseError">
      <div class="result-box error">
        <span class="result-icon">❌</span>
        <div>
          <div class="result-title">文件解析失败</div>
          <div class="result-detail">{{ parseError }}</div>
        </div>
      </div>
    </template>

    <template v-else-if="parsed">
      <div class="section-title">解析结果</div>
      <div class="stat-grid">
        <div class="stat">
          <span class="stat-k">Jobs</span>
          <span class="stat-v">{{ parsed.jobs.length }}</span>
        </div>
        <div class="stat">
          <span class="stat-k">文件机器数</span>
          <span class="stat-v">{{ parsed.machines ?? '—' }}</span>
        </div>
        <div class="stat">
          <span class="stat-k">当前工厂机器数</span>
          <span class="stat-v">{{ factoryMachineCount }}</span>
        </div>
        <div class="stat">
          <span class="stat-k">候选机器分布</span>
          <span class="stat-v small">min={{ candidateDist.min }}, max={{ candidateDist.max }}, avg={{ candidateDist.avg }}</span>
        </div>
      </div>

      <!-- 机器数不匹配警告 -->
      <div v-if="machineMismatch" class="warning-bar">
        ⚠️ 文件机器数({{ parsed.machines }})与当前工厂({{ factoryMachineCount }})不匹配，提交后端将拒绝
      </div>

      <!-- Job 列表预览(前 5) -->
      <div class="section-title">Job 列表预览(前 5)</div>
      <div class="job-preview-list">
        <div v-for="(job, i) in parsed.jobs.slice(0, 5)" :key="i" class="job-preview-row">
          <span class="job-idx">[{{ i }}]</span>
          <span class="job-ops">ops={{ job.length }}</span>
          <span class="job-cands">
            候选=[{{ job.map(op => op.length).join(', ') }}]
          </span>
        </div>
        <div v-if="parsed.jobs.length > 5" class="job-preview-more">
          ...共 {{ parsed.jobs.length }} 个 job
        </div>
      </div>

      <!-- 提交按钮 -->
      <div class="action-row">
        <button
          class="primary-btn"
          :disabled="submitting"
          @click="submit"
        >
          {{ submitting ? '提交中...' : `提交插单 (${parsed.jobs.length} jobs)` }}
        </button>
        <button class="ghost-btn" :disabled="submitting" @click="reset">清空</button>
      </div>
    </template>

    <!-- 上次结果 -->
    <template v-if="lastResult">
      <div class="section-title">上次结果</div>
      <div class="result-box" :class="lastResult.status">
        <div class="result-row">
          <span class="result-icon">
            {{ lastResult.status === 'ok' ? '✅' : lastResult.status === 'partial' ? '⚠️' : '❌' }}
          </span>
          <span class="result-title">
            {{ statusText(lastResult.status) }}
          </span>
        </div>
        <div v-if="lastResult.inserted?.length" class="result-detail">
          已插入：job_id = {{ insertedRange }}
          <span class="muted">({{ lastResult.inserted.length }} 个)</span>
        </div>
        <div v-if="lastResult.rejected?.length" class="result-detail">
          拒绝：{{ lastResult.rejected.length }} 个
          <ul class="rejected-list">
            <li v-for="(r, i) in lastResult.rejected" :key="i">
              job[{{ r.index }}]: {{ r.message }}
            </li>
          </ul>
        </div>
        <div v-if="lastResult.message" class="result-detail">{{ lastResult.message }}</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useFactoryStore } from '@/stores/factory'
import { apiPost, API_ROUTES } from '@/utils/api'

const store = useFactoryStore()

const fileInputRef = ref(null)
const isDragging = ref(false)
const parsed = ref(null)        // { machines, jobs, extensions? }
const parseError = ref('')
const submitting = ref(false)
const lastResult = ref(null)    // { status, inserted, rejected, message? }

// 当前工厂机器数（从 topologyConfig 数 keys；可能为 0 表示未知）
const factoryMachineCount = computed(() => {
  const machines = store.currentTopologyConfig?.machines
  if (!machines) return 0
  return Object.keys(machines).length
})

const machineMismatch = computed(() => {
  if (!parsed.value?.machines) return false
  if (!factoryMachineCount.value) return false  // 当前工厂未知，不算冲突
  return parsed.value.machines !== factoryMachineCount.value
})

// 候选机器分布
const candidateDist = computed(() => {
  if (!parsed.value?.jobs) return { min: 0, max: 0, avg: 0 }
  const all = parsed.value.jobs.flat().map(op => op.length)
  if (!all.length) return { min: 0, max: 0, avg: 0 }
  const sum = all.reduce((a, b) => a + b, 0)
  return {
    min: Math.min(...all),
    max: Math.max(...all),
    avg: +(sum / all.length).toFixed(2),
  }
})

const insertedRange = computed(() => {
  const ids = (lastResult.value?.inserted || []).map(x => x.job_id).sort((a, b) => a - b)
  if (!ids.length) return ''
  if (ids.length === 1) return String(ids[0])
  return `${ids[0]}..${ids[ids.length - 1]}`
})

function triggerFileInput() {
  if (!parsed.value || parseError.value) fileInputRef.value?.click()
}

function handleFileSelect(e) {
  const file = e.target.files?.[0]
  e.target.value = ''  // 允许重复选同一文件
  if (file) readFile(file)
}

function handleDrop(e) {
  isDragging.value = false
  const file = e.dataTransfer.files?.[0]
  if (file) readFile(file)
}

function readFile(file) {
  parseError.value = ''
  lastResult.value = null
  const reader = new FileReader()
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result)
      const validated = validateFjspBody(data)
      parsed.value = validated
    } catch (e) {
      parseError.value = `JSON 解析失败: ${e.message}`
      parsed.value = null
    }
  }
  reader.onerror = () => {
    parseError.value = '文件读取失败'
    parsed.value = null
  }
  reader.readAsText(file)
}

/** 轻量前端预校验，对齐后端 _parse_insert_body 语义。 */
function validateFjspBody(data) {
  if (!data || typeof data !== 'object') {
    throw new Error('文件内容必须是 JSON 对象')
  }
  if (!Array.isArray(data.jobs) || data.jobs.length === 0) {
    throw new Error('文件缺少 jobs 字段，或 jobs 为空（应为 FJSP 实例格式）')
  }
  // 顶层字段
  return {
    machines: typeof data.machines === 'number' ? data.machines : null,
    jobs: data.jobs,
    extensions: data.extensions || {},
  }
}

async function submit() {
  if (!parsed.value || submitting.value) return
  submitting.value = true
  try {
    const body = {
      jobs: parsed.value.jobs,
    }
    if (parsed.value.machines != null) body.machines = parsed.value.machines
    if (parsed.value.extensions && Object.keys(parsed.value.extensions).length) {
      body.extensions = parsed.value.extensions
    }
    const resp = await apiPost(API_ROUTES.FACTORY_CONTROL_INSERT_JOBS, body)
    lastResult.value = resp
    if (resp.status === 'ok') {
      ElMessage.success(`已插单 ${resp.inserted?.length || 0} 个 job`)
    } else if (resp.status === 'partial') {
      ElMessage.warning(`部分成功：${resp.inserted?.length || 0} 入队，${resp.rejected?.length || 0} 拒绝`)
    } else {
      ElMessage.error(resp.message || '插单失败')
    }
  } catch (e) {
    lastResult.value = { status: 'error', message: e.message || '网络错误', inserted: [], rejected: [] }
    ElMessage.error('插单请求失败')
  } finally {
    submitting.value = false
  }
}

function reset() {
  parsed.value = null
  parseError.value = ''
  lastResult.value = null
}

function statusText(s) {
  if (s === 'ok') return '提交成功'
  if (s === 'partial') return '部分成功'
  return '提交失败'
}
</script>

<style scoped>
.job-insert-panel {
  padding: 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 12px;
}

/* 拖拽区 */
.drop-zone {
  border: 1.5px dashed rgba(100, 180, 255, 0.3);
  border-radius: 8px;
  padding: 18px 12px;
  text-align: center;
  cursor: pointer;
  background: rgba(20, 25, 45, 0.4);
  transition: all 0.2s;
}
.drop-zone:hover, .drop-zone.active {
  border-color: rgba(100, 180, 255, 0.6);
  background: rgba(100, 180, 255, 0.08);
}
.drop-content { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.drop-icon { font-size: 24px; opacity: 0.7; }
.drop-title { margin: 0; color: rgba(200, 220, 255, 0.9); font-weight: 500; }
.drop-hint { margin: 0; font-size: 10px; color: rgba(160, 190, 230, 0.5); }

/* 区块标题 */
.section-title {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: rgba(100, 180, 255, 0.7);
  border-top: 1px dashed rgba(255, 255, 255, 0.06);
  padding-top: 8px;
  margin-top: 4px;
}

/* 统计网格 */
.stat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 4px;
}
.stat-k { font-size: 10px; color: rgba(160, 190, 230, 0.6); }
.stat-v { font-size: 13px; color: rgba(220, 230, 245, 0.95); font-weight: 600; font-variant-numeric: tabular-nums; }
.stat-v.small { font-size: 11px; font-weight: 500; }

/* 警告条 */
.warning-bar {
  padding: 6px 10px;
  background: rgba(255, 180, 80, 0.12);
  border: 1px solid rgba(255, 180, 80, 0.3);
  border-radius: 4px;
  color: #ffb450;
  font-size: 11px;
}

/* job 预览列表 */
.job-preview-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 140px;
  overflow-y: auto;
}
.job-preview-row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 3px 6px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 3px;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.job-idx { color: rgba(100, 180, 255, 0.9); font-weight: 600; min-width: 30px; }
.job-ops { color: rgba(220, 230, 245, 0.85); }
.job-cands { color: rgba(160, 190, 230, 0.7); font-size: 10px; }
.job-preview-more {
  font-size: 10px;
  color: rgba(160, 190, 230, 0.5);
  text-align: center;
  padding: 2px;
}

/* 操作按钮 */
.action-row { display: flex; gap: 6px; margin-top: 4px; }
.primary-btn {
  flex: 1;
  padding: 8px 0;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.5), rgba(118, 75, 162, 0.5));
  border: 1px solid rgba(102, 126, 234, 0.3);
  border-radius: 6px;
  color: rgba(220, 230, 245, 0.95);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.primary-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.7), rgba(118, 75, 162, 0.7));
}
.primary-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ghost-btn {
  padding: 8px 14px;
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  color: rgba(160, 190, 230, 0.7);
  font-size: 12px;
  cursor: pointer;
}
.ghost-btn:hover:not(:disabled) { background: rgba(255, 255, 255, 0.05); }
.ghost-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* 结果框 */
.result-box {
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 11px;
}
.result-box.ok { background: rgba(102, 208, 106, 0.08); border: 1px solid rgba(102, 208, 106, 0.2); }
.result-box.partial { background: rgba(255, 180, 80, 0.08); border: 1px solid rgba(255, 180, 80, 0.2); }
.result-box.error { background: rgba(255, 100, 100, 0.08); border: 1px solid rgba(255, 100, 100, 0.2); }
.result-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.result-icon { font-size: 14px; }
.result-title { font-weight: 600; color: rgba(220, 230, 245, 0.95); }
.result-detail { color: rgba(180, 200, 220, 0.85); margin-left: 20px; font-size: 10px; }
.result-detail .muted { color: rgba(160, 190, 230, 0.5); margin-left: 4px; }
.rejected-list { margin: 4px 0 0 16px; padding: 0; color: rgba(255, 180, 180, 0.85); }
.rejected-list li { margin: 1px 0; }
</style>
