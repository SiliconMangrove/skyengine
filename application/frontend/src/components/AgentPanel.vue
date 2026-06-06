<template>
  <div class="agent-panel">
    <div class="agent-header">
      <h3>🤖 Agent 分析</h3>
      <span class="agent-counter" v-if="agvList.length > 0">
        {{ activeCount }}/{{ agvList.length }} 活跃
      </span>
    </div>

    <div v-if="agvList.length === 0" class="agent-empty">
      <span>暂无 Agent 数据</span>
    </div>

    <div v-else class="agent-list">
      <div
        v-for="agv in agvList"
        :key="agv.id"
        class="agent-card"
        :class="{ active: agv._active, idle: !agv._active }"
      >
        <div class="agent-card-header">
          <span class="agent-icon">🤖</span>
          <span class="agent-name">{{ agv.name || `AGV-${agv.id}` }}</span>
          <span class="agent-status-dot" :class="agv._active ? 'dot-active' : 'dot-idle'"></span>
        </div>
        <div class="agent-card-body">
          <div class="agent-field">
            <span class="agent-field-label">位置</span>
            <span class="agent-field-value">
              {{ agv._pos ? `(${agv._pos[0]}, ${agv._pos[1]})` : '--' }}
            </span>
          </div>
          <div class="agent-field">
            <span class="agent-field-label">速度</span>
            <span class="agent-field-value">{{ agv.velocity ?? '--' }}</span>
          </div>
          <div class="agent-field">
            <span class="agent-field-label">容量</span>
            <span class="agent-field-value">{{ agv.capacity ?? '--' }}</span>
          </div>
          <div class="agent-field">
            <span class="agent-field-label">状态</span>
            <span class="agent-field-value status-tag" :class="agv._active ? 'status-active' : 'status-idle'">
              {{ agv._active ? '运行中' : (agv.status || '空闲') }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="agent-summary" v-if="agvList.length > 0">
      <div class="summary-item">
        <span class="summary-label">总 Agent</span>
        <span class="summary-value">{{ agvList.length }}</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">活跃</span>
        <span class="summary-value active-text">{{ activeCount }}</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">空闲</span>
        <span class="summary-value idle-text">{{ agvList.length - activeCount }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useFactoryStore } from '@/stores/factory'

const store = useFactoryStore()

const agvList = computed(() => {
  const configAgvs = store.getAGVs()
  const snapshot = store.currentState
  const positions = snapshot?.grid_state?.positions_xy ?? []
  const isActive = snapshot?.grid_state?.is_active ?? []

  return configAgvs.map((agv, i) => ({
    ...agv,
    _pos: positions[i] ?? agv.initialLocation ?? null,
    _active: isActive[i] ?? false,
  }))
})

const activeCount = computed(() => agvList.value.filter(a => a._active).length)
</script>

<style scoped>
.agent-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  color: rgba(200, 220, 255, 0.85);
  font-size: 12px;
}

.agent-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 6px;
}

.agent-header h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: rgba(200, 220, 255, 0.9);
}

.agent-counter {
  font-size: 11px;
  color: rgba(100, 180, 255, 0.7);
  background: rgba(100, 180, 255, 0.1);
  padding: 2px 8px;
  border-radius: 10px;
}

.agent-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(160, 190, 230, 0.4);
  font-size: 12px;
  padding: 24px;
}

.agent-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.agent-list::-webkit-scrollbar {
  width: 4px;
}

.agent-list::-webkit-scrollbar-track {
  background: transparent;
}

.agent-list::-webkit-scrollbar-thumb {
  background: rgba(100, 180, 255, 0.2);
  border-radius: 2px;
}

.agent-card {
  background: rgba(100, 180, 255, 0.06);
  border: 1px solid rgba(100, 180, 255, 0.12);
  border-radius: 8px;
  padding: 8px 10px;
  transition: border-color 0.2s;
}

.agent-card:hover {
  border-color: rgba(100, 180, 255, 0.25);
}

.agent-card.active {
  border-left: 3px solid #4CAF50;
}

.agent-card.idle {
  border-left: 3px solid rgba(100, 180, 255, 0.2);
}

.agent-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.agent-icon {
  font-size: 14px;
}

.agent-name {
  flex: 1;
  font-size: 12px;
  font-weight: 600;
  color: rgba(200, 220, 255, 0.9);
}

.agent-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.dot-active {
  background: #4CAF50;
  box-shadow: 0 0 6px rgba(76, 175, 80, 0.5);
}

.dot-idle {
  background: rgba(160, 190, 230, 0.3);
}

.agent-card-body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 12px;
}

.agent-field {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.agent-field-label {
  font-size: 11px;
  color: rgba(160, 190, 230, 0.5);
}

.agent-field-value {
  font-size: 11px;
  color: rgba(200, 220, 255, 0.8);
  font-variant-numeric: tabular-nums;
}

.status-tag {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
}

.status-active {
  background: rgba(76, 175, 80, 0.15);
  color: #4CAF50;
}

.status-idle {
  background: rgba(160, 190, 230, 0.1);
  color: rgba(160, 190, 230, 0.5);
}

.agent-summary {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid rgba(100, 180, 255, 0.1);
  background: rgba(100, 180, 255, 0.03);
}

.summary-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.summary-label {
  font-size: 10px;
  color: rgba(160, 190, 230, 0.5);
}

.summary-value {
  font-size: 14px;
  font-weight: 700;
  color: rgba(200, 220, 255, 0.9);
}

.active-text {
  color: #4CAF50;
}

.idle-text {
  color: rgba(160, 190, 230, 0.5);
}
</style>
