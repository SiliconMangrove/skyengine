<template>
  <Teleport to="body">
  <DraggablePanel
    v-if="visible"
    title="🎯 焦点"
    :width="FP_WIDTH"
    :max-height="520"
    :collapsible="false"
    :initial-pos="initialPos"
    @close="handleClose"
  >
    <!-- Tab 栏（sticky：内容滚动时固定在顶部） -->
    <div class="fp-tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="fp-tab"
        :class="{ active: activeTab === t.key, has_sel: hasSelection(t.key) }"
        @click="activeTab = t.key"
      >
        <span class="fp-tab-icon">{{ t.icon }}</span>
        <span class="fp-tab-label">{{ t.label }}</span>
        <span v-if="hasSelection(t.key)" class="fp-dot"></span>
      </button>
    </div>

    <!-- 内容区 -->
    <div class="fp-content">
      <!-- Machine -->
      <template v-if="activeTab === 'machine'">
        <template v-if="selectedMachine">
          <div class="fp-head">
            <span class="fp-title">⚙️ {{ selectedMachineKey }}</span>
            <span class="fp-tag" :class="`tag-${machineStatusClass}`">{{ selectedMachine.status || (selectedMachine.current_op ? 'WORKING' : 'IDLE') }}</span>
          </div>

          <div class="fp-row">
            <span class="fp-k">位置</span>
            <span class="fp-v">{{ fmtLoc(selectedMachine.location) }}</span>
          </div>
          <div class="fp-row">
            <span class="fp-k">队列</span>
            <span class="fp-v">{{ selectedMachine.queue_length ?? 0 }}</span>
          </div>

          <template v-if="selectedMachine.current_op">
            <div class="fp-section">当前工序</div>
            <div class="fp-row">
              <span class="fp-k">Job-Op</span>
              <span class="fp-v">Job{{ selectedMachine.current_op.job_id }}-Op{{ selectedMachine.current_op.op_id }}</span>
            </div>
            <div class="fp-progress">
              <div class="fp-progress-head">
                <span>Op 步数</span>
                <span>{{ selectedMachine.current_op.step_done }}/{{ selectedMachine.current_op.proc_time }}</span>
              </div>
              <div class="fp-bar-wrap">
                <div class="fp-bar bar-op" :style="{ width: opStepPct + '%' }"></div>
              </div>
            </div>
            <div class="fp-progress">
              <div class="fp-progress-head">
                <span>Job 进度</span>
                <span>{{ (selectedMachine.current_op.index_in_job ?? 0) + 1 }}/{{ selectedMachine.current_op.total_in_job }}</span>
              </div>
              <div class="fp-bar-wrap">
                <div class="fp-bar bar-job" :style="{ width: opJobPct + '%' }"></div>
              </div>
            </div>
          </template>
          <div v-else class="fp-empty-inline">空闲</div>
        </template>
        <div v-else class="fp-placeholder">
          <span class="fp-ph-icon">⚙️</span>
          <p>点击机器卡片查看详情</p>
        </div>
      </template>

      <!-- Job -->
      <template v-else-if="activeTab === 'job'">
        <template v-if="selectedJob">
          <div class="fp-head">
            <span class="fp-title">📦 Job{{ selectedJob.job_id }}</span>
            <span class="fp-tag" :class="selectedJob.is_completed ? 'tag-completed' : 'tag-progress'">
              {{ selectedJob.is_completed ? '已完成' : `${selectedJob.progress.done}/${selectedJob.progress.total}` }}
            </span>
          </div>

          <div class="fp-row">
            <span class="fp-k">交期</span>
            <span class="fp-v">{{ selectedJob.due ?? '—' }}</span>
          </div>
          <div class="fp-row">
            <span class="fp-k">完工</span>
            <span class="fp-v">{{ selectedJob.completion_time ?? '—' }}</span>
          </div>
          <div class="fp-row">
            <span class="fp-k">释放</span>
            <span class="fp-v">{{ selectedJob.release ?? '—' }}</span>
          </div>

          <div class="fp-progress">
            <div class="fp-progress-head">
              <span>总进度</span>
              <span>{{ selectedJob.progress.done }}/{{ selectedJob.progress.total }}</span>
            </div>
            <div class="fp-bar-wrap">
              <div class="fp-bar bar-job" :style="{ width: jobPct + '%' }"></div>
            </div>
          </div>

          <div class="fp-section">工序列表 ({{ selectedJob.ops.length }})</div>
          <div class="fp-op-list">
            <div
              v-for="op in selectedJob.ops"
              :key="op.op_id"
              class="fp-op-row"
              :class="`op-${(op.status || '').toLowerCase()}`"
            >
              <span class="fp-op-id">Op{{ op.op_id }}</span>
              <span class="fp-op-status">{{ op.status }}</span>
              <span class="fp-op-mach">{{ op.assigned_machine != null ? 'M' + op.assigned_machine : '—' }}</span>
              <span class="fp-op-time">{{ op.proc_time }}t</span>
            </div>
          </div>
        </template>
        <div v-else class="fp-placeholder">
          <span class="fp-ph-icon">📦</span>
          <p>点击 Job 卡片查看详情</p>
        </div>
      </template>

      <!-- AGV -->
      <template v-else-if="activeTab === 'agv'">
        <template v-if="selectedAgvIndex != null && agvCount > 0">
          <div class="fp-head">
            <span class="fp-title">🤖 AGV-{{ selectedAgvIndex }}</span>
            <span class="fp-tag" :class="selectedAgvActive ? 'tag-working' : 'tag-idle'">
              {{ selectedAgvActive ? '执行中' : '空闲' }}
            </span>
          </div>
          <div class="fp-row">
            <span class="fp-k">坐标</span>
            <span class="fp-v">[{{ selectedAgvPos[0] }}, {{ selectedAgvPos[1] }}]</span>
          </div>
          <div class="fp-row">
            <span class="fp-k">状态</span>
            <span class="fp-v">{{ selectedAgvActive ? '执行任务中' : '空闲' }}</span>
          </div>
        </template>
        <div v-else-if="agvCount > 0" class="fp-placeholder">
          <span class="fp-ph-icon">🤖</span>
          <p>点击 AGV 卡片查看详情</p>
        </div>
        <div v-else class="fp-placeholder">
          <span class="fp-ph-icon">🤖</span>
          <p>等待 AGV 数据...</p>
        </div>
      </template>
    </div>
  </DraggablePanel>

  <!-- 收起后的浮标（有内容可看时才显示） -->
  <button v-else-if="showPill" class="focus-panel-collapsed" title="展开焦点面板" @click="reopen">
    🎯
  </button>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useFactoryStore } from '@/stores/factory'
import DraggablePanel from './DraggablePanel.vue'

const store = useFactoryStore()

const FP_WIDTH = 280
const FP_HEIGHT = 480
// 初始位置：左下角，与 FactoryTabsPanel（x=12, width=300, height=480）左对齐
// FactoryTabsPanel 占 y∈[12, 492]，FocusPanel 接在正下方
const FP_OFFSET_TOP = 12 + 480 + 8  // = 500
const initialPos = {
  x: 12,
  y: Math.max(FP_OFFSET_TOP, (typeof window !== 'undefined' ? window.innerHeight : 800) - FP_HEIGHT - 12),
}

// 收起/展开：closed 与选中态解耦，× 真正能关掉
// pinned 默认 true：页面加载后直接显示占位面板，不依赖选中态
const closed = ref(false)
const pinned = ref(true)

const tabs = [
  { key: 'machine', label: '机器', icon: '⚙️' },
  { key: 'job',     label: 'Job', icon: '📦' },
  { key: 'agv',     label: 'AGV', icon: '🤖' },
]

const activeTab = ref('machine')

const selectedMachineKey = computed(() => store.selectedMachineKey)
const selectedJobId = computed(() => store.selectedJobId)
const selectedAgvIndex = computed(() => store.selectedAgvIndex)

const selectedMachine = computed(() =>
  selectedMachineKey.value ? (store.currentState?.machines?.[selectedMachineKey.value] ?? null) : null
)
const selectedJob = computed(() =>
  selectedJobId.value != null ? (store.currentState?.jobs?.find(j => j.job_id === selectedJobId.value) ?? null) : null
)

const machineStatusClass = computed(() => {
  const m = selectedMachine.value
  if (!m) return 'idle'
  const status = m.status || (m.current_op ? 'WORKING' : 'IDLE')
  return status.toLowerCase()
})

const opStepPct = computed(() => {
  const op = selectedMachine.value?.current_op
  return op && op.proc_time > 0 ? Math.min(100, Math.round((op.step_done / op.proc_time) * 100)) : 0
})
const opJobPct = computed(() => {
  const op = selectedMachine.value?.current_op
  return op && op.total_in_job > 0 ? Math.min(100, Math.round(((op.index_in_job + 1) / op.total_in_job) * 100)) : 0
})
const jobPct = computed(() => {
  const j = selectedJob.value
  return j && j.progress.total > 0 ? Math.round((j.progress.done / j.progress.total) * 100) : 0
})

// AGV 派生
const positions = computed(() => store.currentState?.grid_state?.positions_xy || [])
const isActiveArr = computed(() => store.currentState?.grid_state?.is_active || [])
const agvCount = computed(() => positions.value.length)
function agvIsActive(i) { return isActiveArr.value[i] === true }
const selectedAgvPos = computed(() => positions.value[selectedAgvIndex.value] || [0, 0])
const selectedAgvActive = computed(() => agvIsActive(selectedAgvIndex.value))

const hasAnySelection = computed(() =>
  selectedMachineKey.value != null ||
  selectedJobId.value != null ||
  selectedAgvIndex.value != null
)

// 显式可见性：默认始终显示（占位），× 关闭后变浮标
const visible = computed(() => !closed.value)
const showPill = computed(() => closed.value)

import { onMounted as _onMounted } from 'vue'
_onMounted(() => { console.log('[FocusPanel] mounted, visible=', visible.value) })
watch(visible, (v) => console.log('[FocusPanel] visible ->', v))

function hasSelection(tabKey) {
  if (tabKey === 'machine') return selectedMachineKey.value != null
  if (tabKey === 'job') return selectedJobId.value != null
  if (tabKey === 'agv') return selectedAgvIndex.value != null
  return false
}

function handleClose() { closed.value = true }
function reopen() { closed.value = false; pinned.value = true }

// 选中实体 → 切 tab + 弹出（覆盖 closed）
watch(selectedMachineKey, (v) => { if (v != null) { activeTab.value = 'machine'; reopen() } })
watch(selectedJobId,     (v) => { if (v != null) { activeTab.value = 'job'; reopen() } })
watch(selectedAgvIndex,  (v) => { if (v != null) { activeTab.value = 'agv'; reopen() } })

function fmtLoc(loc) {
  if (!loc) return '—'
  if (Array.isArray(loc)) return `[${loc[0]}, ${loc[1]}]`
  return String(loc)
}
</script>

<style scoped>
/* Tab 栏：sticky 固定在 DraggablePanel.dp-body 顶部，内容滚动时不消失 */
.fp-tabs {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: stretch;
  background: rgba(10, 14, 28, 0.95);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.fp-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 8px 4px;
  background: none;
  border: none;
  color: #8090a0;
  cursor: pointer;
  font-size: 11px;
  transition: all .15s;
  position: relative;
}
.fp-tab:hover { color: #c0d0e0; background: rgba(255, 255, 255, 0.03); }
.fp-tab.active { color: #64b5ff; background: rgba(100, 181, 255, 0.08); }
.fp-tab.active::after {
  content: '';
  position: absolute;
  left: 12%; right: 12%; bottom: 0;
  height: 2px;
  background: #64b5ff;
  border-radius: 1px;
}
.fp-tab-icon { font-size: 13px; }
.fp-tab-label { font-weight: 600; }
.fp-dot {
  width: 5px; height: 5px;
  border-radius: 50%;
  background: #66d06a;
  margin-left: 2px;
}

/* 内容区（dp-body 已提供滚动，这里只管排版） */
.fp-content {
  padding: 10px 12px 12px;
}

.fp-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.fp-title { font-size: 14px; font-weight: 600; color: #e8eef6; }
.fp-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  color: #b0b8c0;
}
.tag-working { background: rgba(100, 181, 255, 0.2); color: #64b5ff; }
.tag-idle    { background: rgba(160, 160, 160, 0.18); color: #a0a0a0; }
.tag-completed { background: rgba(102, 208, 106, 0.2); color: #66d06a; }
.tag-progress  { background: rgba(100, 181, 255, 0.2); color: #64b5ff; }
.tag-broken  { background: rgba(255, 100, 100, 0.2); color: #ff6464; }
.tag-blocked { background: rgba(255, 180, 80, 0.2); color: #ffb450; }

.fp-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
  font-variant-numeric: tabular-nums;
}
.fp-k { color: #8090a0; }
.fp-v { color: #d0dae8; font-weight: 500; }
.fp-empty-inline { color: #707070; font-style: italic; padding: 4px 0; }

.fp-section {
  margin-top: 10px;
  margin-bottom: 4px;
  font-size: 10px;
  color: #64b5ff;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-top: 1px dashed rgba(255, 255, 255, 0.06);
  padding-top: 8px;
}

.fp-progress { margin: 6px 0; }
.fp-progress-head {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: #9098a0;
  margin-bottom: 3px;
  font-variant-numeric: tabular-nums;
}
.fp-bar-wrap {
  height: 5px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 3px;
  overflow: hidden;
}
.fp-bar { height: 100%; border-radius: 3px; transition: width .25s; }
.bar-op  { background: linear-gradient(90deg, #64b5ff, #4a90e2); }
.bar-job { background: linear-gradient(90deg, #66d06a, #4CAF50); }

/* job op list */
.fp-op-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.fp-op-row {
  display: grid;
  grid-template-columns: 40px 70px 1fr auto;
  gap: 6px;
  align-items: center;
  padding: 3px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.03);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.fp-op-id { font-weight: 600; color: #c0c8d0; }
.fp-op-status { color: #8090a0; }
.fp-op-mach { color: #b0d0ff; }
.fp-op-time { color: #808890; text-align: right; }
.fp-op-row.op-finished .fp-op-status { color: #66d06a; }
.fp-op-row.op-processing .fp-op-status { color: #64b5ff; }
.fp-op-row.op-pending .fp-op-status { color: #a0a0a0; }

/* placeholder */
.fp-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px 8px;
  color: #606870;
  text-align: center;
}
.fp-ph-icon { font-size: 28px; opacity: 0.5; margin-bottom: 6px; }
.fp-placeholder p { margin: 0; font-size: 11px; }

/* 收起浮标（fixed 与 DraggablePanel 对齐） */
.focus-panel-collapsed {
  position: fixed;
  top: 12px;
  right: 12px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: rgba(20, 26, 38, 0.92);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(100, 181, 255, 0.3);
  color: #64b5ff;
  cursor: pointer;
  font-size: 16px;
  z-index: 100;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  transition: transform .15s;
}
.focus-panel-collapsed:hover { transform: scale(1.08); }
</style>
