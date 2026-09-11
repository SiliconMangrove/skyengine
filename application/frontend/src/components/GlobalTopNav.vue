<template>
  <div v-if="visibleBtn" class="global-topnav">
    <!-- 工作空间选择页：算法池 -->
    <button
      v-if="visibleBtn === 'algo'"
      class="nav-pill"
      :class="{ active: ui.algoPoolOpen }"
      @click="ui.toggleAlgoPool()"
      title="算法池"
    >
      <span class="pill-icon">🧠</span>
      <span class="pill-text">算法池</span>
    </button>

    <!-- 具体工厂操作页：批处理；不支持时禁用并提示原因 -->
    <button
      v-else-if="visibleBtn === 'batch'"
      class="nav-pill"
      :class="{ active: ui.batchOpen, disabled: !batchSupported }"
      :disabled="!batchSupported"
      @click="onBatchClick"
      :title="batchTitle"
    >
      <span class="pill-icon">📊</span>
      <span class="pill-text">批处理</span>
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useUiPanelStore } from '@/stores/uiPanel'
import { useFactoryStore } from '@/stores/factory'

const ui = useUiPanelStore()
const factoryStore = useFactoryStore()

// 导航状态：'home'（首页）不显示按钮；'selector'（工作空间选择页）显示算法池；'factory'（具体工厂）显示批处理
// factoryLoading=true 期间（工厂进入/退出加载过渡）一律隐藏，规避 navState race
const visibleBtn = computed(() => {
  if (ui.factoryLoading) return null
  if (ui.navState === 'selector') return 'algo'
  if (ui.navState === 'factory') return 'batch'
  return null // home / 其他：不渲染
})

// 后端 /batch/* 全部走 DockerProxy；只有 DockerProxy 支撑的工厂才能批处理。
// packet_factory / grid_factory_new 都是 DockerProxy 后端，static 不是。
const BATCH_SUPPORTED = new Set(['packet_factory', 'grid_factory_new'])

const currentFactoryId = computed(() => factoryStore.currentFactory?.id)
const batchSupported = computed(() =>
  !!currentFactoryId.value && BATCH_SUPPORTED.has(currentFactoryId.value)
)

const batchTitle = computed(() => {
  if (batchSupported.value) return '批处理实验'
  if (!currentFactoryId.value) return '请先选择工厂'
  return `当前工厂（${currentFactoryId.value}）不支持批处理。`
})

function onBatchClick() {
  if (!batchSupported.value) {
    ElMessage.warning(
      `当前工厂不支持批处理：仅容器化（Docker）工厂可用，请切换到翼辉电池装配产线 / 容器化仿真工作台`
    )
    return
  }
  ui.toggleBatch()
}
</script>

<style scoped>
.global-topnav {
  position: fixed;
  top: 16px;
  right: 20px;
  z-index: 1000;
  display: flex;
  gap: 6px;
  padding: 5px;
  background: rgba(20, 25, 45, 0.65);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
}

.nav-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  color: rgba(200, 220, 255, 0.85);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s;
}
.nav-pill:hover {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.1);
  color: rgba(230, 240, 255, 0.98);
}
.nav-pill.active {
  background: rgba(100, 180, 255, 0.22);
  border-color: rgba(100, 180, 255, 0.6);
  color: rgba(140, 210, 255, 1);
  box-shadow: inset 0 0 8px rgba(100, 180, 255, 0.2);
}
.nav-pill.disabled,
.nav-pill:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.nav-pill.disabled:hover {
  background: transparent;
  border-color: transparent;
  color: rgba(200, 220, 255, 0.85);
}
.pill-icon {
  font-size: 13px;
  line-height: 1;
}
.pill-text {
  font-weight: 500;
  letter-spacing: 0.2px;
}
</style>
