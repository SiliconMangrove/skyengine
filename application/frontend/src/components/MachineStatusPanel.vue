<template>
  <div class="machine-status-panel">
    <div class="panel-header">
      <h3>⚙️ 机器状态</h3>
      <span class="hint" v-if="!hasData">等待 frame 数据...</span>
    </div>

    <div v-if="hasData" class="machine-list">
      <div
        v-for="m in machineList"
        :key="m.key"
        class="machine-card"
        :class="[
          `status-${m.statusClass}`,
          { selected: m.key === store.selectedMachineKey }
        ]"
        @click="store.selectMachine(m.key)"
      >
        <div class="card-head">
          <span class="m-id">{{ m.key }}</span>
          <span class="m-status" :class="`tag-${m.statusClass}`">{{ m.statusLabel }}</span>
          <span v-if="m.queueLength > 0" class="m-queue">📥 {{ m.queueLength }}</span>
        </div>

        <template v-if="m.current_op">
          <div class="progress-row">
            <span class="progress-label">Op 步数</span>
            <div class="bar-wrap">
              <div class="bar bar-op" :style="{ width: m.opStepPct + '%' }"></div>
            </div>
            <span class="progress-val">{{ m.current_op.step_done }}/{{ m.current_op.proc_time }}</span>
          </div>
          <div class="progress-row">
            <span class="progress-label">JOB 进度</span>
            <div class="bar-wrap">
              <div class="bar bar-job" :style="{ width: m.opJobPct + '%' }"></div>
            </div>
            <span class="progress-val">{{ m.current_op.index_in_job + 1 }}/{{ m.current_op.total_in_job }}</span>
          </div>
          <div class="op-meta">
            Job{{ m.current_op.job_id }}-Op{{ m.current_op.op_id }}
            <button class="link-btn" @click.stop="store.selectJob(m.current_op.job_id)">
              → 查看 Job
            </button>
          </div>
        </template>
        <div v-else class="op-meta idle-meta">空闲</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useFactoryStore } from '@/stores/factory'

const store = useFactoryStore()

const machineList = computed(() => {
  const machines = store.currentState?.machines || {}
  return Object.entries(machines).map(([key, m]) => {
    const op = m.current_op
    const status = m.status || (op ? 'WORKING' : 'IDLE')
    return {
      key,
      statusLabel: status,
      statusClass: status.toLowerCase(),
      current_op: op,
      queueLength: m.queue_length || 0,
      opStepPct: op && op.proc_time > 0
        ? Math.min(100, Math.round((op.step_done / op.proc_time) * 100))
        : 0,
      opJobPct: op && op.total_in_job > 0
        ? Math.min(100, Math.round(((op.index_in_job + 1) / op.total_in_job) * 100))
        : 0,
    }
  })
})

const hasData = computed(() => machineList.value.length > 0)
</script>

<style scoped>
.machine-status-panel {
  height: 100%;
  overflow-y: auto;
  padding: 8px;
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.panel-header h3 { font-size: 14px; margin: 0; color: #c0d0e0; }
.hint { font-size: 11px; color: #707070; }

.machine-list { display: flex; flex-direction: column; gap: 6px; }
.machine-card {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
  transition: all .15s;
}
.machine-card:hover { background: rgba(255,255,255,0.08); }
.machine-card.selected {
  border-color: #64b5ff;
  box-shadow: 0 0 0 2px rgba(100,181,255,0.25);
}
.machine-card.status-broken { border-color: rgba(255,100,100,0.4); }
.machine-card.status-blocked { border-color: rgba(255,180,80,0.4); }

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.m-id { font-weight: 600; font-size: 13px; color: #e0e8f0; }
.m-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(255,255,255,0.08);
  color: #b0b8c0;
}
.tag-working { background: rgba(100,181,255,0.2); color: #64b5ff; }
.tag-idle { background: rgba(160,160,160,0.18); color: #a0a0a0; }
.tag-blocked { background: rgba(255,180,80,0.2); color: #ffb450; }
.tag-broken { background: rgba(255,100,100,0.2); color: #ff6464; }
.m-queue { font-size: 11px; color: #b0d0ff; margin-left: auto; }

.progress-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 3px 0;
  font-size: 11px;
  color: #b0b8c0;
}
.progress-label { width: 60px; flex-shrink: 0; }
.bar-wrap {
  flex: 1;
  height: 6px;
  background: rgba(255,255,255,0.06);
  border-radius: 3px;
  overflow: hidden;
}
.bar { height: 100%; border-radius: 3px; transition: width .2s; }
.bar-op { background: linear-gradient(90deg, #64b5ff, #4a90e2); }
.bar-job { background: linear-gradient(90deg, #4CAF50, #66d06a); }
.progress-val { width: 50px; text-align: right; font-variant-numeric: tabular-nums; }

.op-meta {
  font-size: 11px;
  color: #9098a0;
  margin-top: 4px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.idle-meta { color: #707070; }
.link-btn {
  background: none;
  border: none;
  color: #64b5ff;
  cursor: pointer;
  font-size: 11px;
  padding: 0;
}
.link-btn:hover { text-decoration: underline; }
</style>
