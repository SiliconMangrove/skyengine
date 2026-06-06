<template>
  <div class="right-side-panel">
    <div class="panel-content">
      <ConfigPanel
        ref="configPanelRef"
        :title="configPanelTitle"
        :playback-speed="playbackSpeed"
        :lerp-factor="lerpFactor"
        :show-paths="showPaths"
        :show-grid="showGrid"
        :show-labels="showLabels"
        :show-status="showStatus"
        @update:playback-speed="$emit('update:playback-speed', $event)"
        @update:lerp-factor="$emit('update:lerp-factor', $event)"
        @update:show-paths="$emit('update:show-paths', $event)"
        @update:show-grid="$emit('update:show-grid', $event)"
        @update:show-labels="$emit('update:show-labels', $event)"
        @update:show-status="$emit('update:show-status', $event)"
        @edit-mode-change="$emit('edit-mode-change', $event)"
      >
        <slot name="config-extra"></slot>
      </ConfigPanel>

      <MetricsPanel :show-chart="showChart">
        <slot name="metrics-extra"></slot>
      </MetricsPanel>

      <AgentPanel />

      <EventPanel :title="eventPanelTitle" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ConfigPanel from './ConfigPanel.vue'
import MetricsPanel from './MetricsPanel.vue'
import EventPanel from './EventPanel.vue'
import AgentPanel from './AgentPanel.vue'

const configPanelRef = ref(null)

defineProps({
  // ConfigPanel 相关 Props 保持不变 (UI 控制类)
  configPanelTitle: { type: String, default: '配置选项' },
  playbackSpeed: { type: Number, default: 1000 },
  lerpFactor: { type: Number, default: 0.15 },
  showPaths: { type: Boolean, default: true },
  showGrid: { type: Boolean, default: true },
  showLabels: { type: Boolean, default: true },
  showStatus: { type: Boolean, default: true },

  // MetricsPanel 相关 (只剩 UI 开关)
  showChart: { type: Boolean, default: true },

  // EventPanel 相关 (只剩标题)
  eventPanelTitle: { type: String, default: '📋 系统事件' },
})

defineEmits([
  'update:playback-speed',
  'update:lerp-factor',
  'update:show-paths',
  'update:show-grid',
  'update:show-labels',
  'update:show-status',
  'edit-mode-change',
])

// 暴露 ConfigPanel 的方法
defineExpose({
  getSelectedFile: () => configPanelRef.value?.getSelectedFile(),
  resetFile: () => configPanelRef.value?.resetFile(),
})
</script>

<style scoped>
@import './styles/RightSidePanel.css';
</style>
