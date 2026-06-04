<template>
  <div class="layout-container">

    <div class="main-content">
      <div v-if="!hideControlPanel" class="panel-area">
        <ControlPanel :disabled="isEditMode" />
      </div>

      <div class="vis-area" :class="{ 'full-width': hideControlPanel }">
        <div v-if="!configLoaded" class="config-placeholder">
          <div class="placeholder-content">
            <div class="placeholder-icon">⚙️</div>
            <h2>待配置工厂环境</h2>
            <p>请在右侧面板上传工厂配置文件（JSON格式）</p>
            <p class="hint">若无可用配置可下载模板文件以做测试</p>
          </div>
        </div>
        <FactoryVisualization3D v-else :static-config="topologyConfig" :baseGridSize="renderConfig.baseGridSize"
          :defaultGridWidth="renderConfig.gridWidth" :defaultGridHeight="renderConfig.gridHeight"
          :editMode="isEditMode" :backgroundTheme="backgroundTheme" :backgroundSize="backgroundSize">
          <template #header>
            <div class="floating-toolbar">
              <div class="toolbar-left">
                <span class="toolbar-title">🏭 仿真工作台</span>
                <span class="divider">|</span>
                <span class="toolbar-label">当前场景: {{ currentScenario === 'safety' ? '安全巡检' : '未选择' }}</span>
              </div>

              <div class="toolbar-right">
                <button v-if="onLoadData" @click="onLoadData" class="glass-btn" title="加载测试数据">
                  📂 加载数据
                </button>
                <button @click="loadSafetyInspection" class="glass-btn"
                  :class="{ active: currentScenario === 'safety' }">
                  📂 加载安全巡检案例
                </button>
              </div>
            </div>
          </template>
        </FactoryVisualization3D>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useFactoryStore } from '@/stores/factory';
import ControlPanel from './ControlPanel.vue';
import FactoryVisualization3D from './FactoryVisualization3D.vue';

const props = defineProps({
  hideControlPanel: {
    type: Boolean,
    default: false
  },
  onLoadData: {
    type: Function,
    default: null
  },
  editMode: {
    type: Boolean,
    default: false
  },
  backgroundTheme: {
    type: String,
    default: 'clean'
  },
  backgroundSize: {
    type: Number,
    default: 2
  }
});

const store = useFactoryStore();
const currentScenario = ref('');
const configLoaded = ref(false);
const isEditMode = computed(() => props.editMode);

// --- 默认空配置（等待用户上传）---
const defaultTopologyConfig = {
  zones: [],
  machines: {},
  waypoints: {}
};

// --- 1. 响应式拓扑配置 ---
// 优先使用已加载的配置，否则使用默认配置
const topologyConfig = computed(() => {
  return store.currentTopologyConfig || defaultTopologyConfig;
});

// 获取渲染配置（包括网格尺寸）
const renderConfig = computed(() => {
  const topology = store.currentTopologyConfig
  const renderCfg = store.currentRenderConfig
  return {
    baseGridSize: renderCfg?.baseGridSize || 40,
    gridWidth: topology?.gridWidth || renderCfg?.gridWidth || 20,
    gridHeight: topology?.gridHeight || renderCfg?.gridHeight || 14
  };
});

// --- 2. 动态任务数据 (Test Cases) ---
const safetyTestCase = [
  { x: 5, y: 2, label: "1. 初始上货 (Loading)", wait: 1000 },
  { x: 5, y: 4, label: "2. 前往 R1 路由点", wait: 0 },
  { x: 8, y: 4, label: "3. T1 顶部供料", wait: 800 },
  { x: 5, y: 4, label: "4. 退回 R1 路由点", wait: 0 },
  { x: 5, y: 6, label: "5. 前往 R2 路由点", wait: 0 },
  { x: 7, y: 6, label: "6. T1 左上工位", wait: 800 },
  { x: 7, y: 9, label: "7. T1 左下工位", wait: 800 },
  { x: 5, y: 9, label: "8. 退回主通道", wait: 0 },
  { x: 5, y: 11, label: "9. 前往底部通道", wait: 0 },
  { x: 13, y: 9, label: "10. T2 左下工位", wait: 800 },
  { x: 13, y: 6, label: "11. T2 左上工位", wait: 800 },
  { x: 17, y: 6, label: "12. 绕行右侧路由", wait: 0 },
  { x: 17, y: 4, label: "13. 准备进入顶部", wait: 0 },
  { x: 14, y: 4, label: "14. T2 顶部供料", wait: 1000 },
  { x: 5, y: 2, label: "15. 返回上货点", wait: 0 }
];

// --- 3. 业务逻辑方法 ---

function loadSafetyInspection() {
  currentScenario.value = 'safety';
  store.reset();
  store.loadCommandQueue(safetyTestCase);
}

// 监听 Store 中的拓扑配置变化
watch(() => store.currentConfigId, (newConfigId) => {
  if (newConfigId) {
    configLoaded.value = true;
    console.log(`✅ 配置已切换，地图已更新: ${newConfigId}`);

    // 自动初始化 AGV
    setTimeout(() => {
      store.initializeAGVs();
    }, 100);
  }
}, { immediate: true });

// 监听拓扑配置的深层变化
watch(() => store.currentTopologyConfig, (newConfig) => {
  if (newConfig && Object.keys(newConfig).length > 0) {
    configLoaded.value = true;
  }
}, { deep: true });

onMounted(() => {
  // 自动从 JSON 配置文件加载拓扑
  // initializeConfig('/configs/packet_factory.json');

  // 可选：初始化时不加载任何 Case，或者默认选中一个
  // loadSafetyInspection(); 
});
</script>

<style scoped>
@import './styles/FactoryPlayerSSE.css';

.config-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background: var(--bg-body, #0a1120);
}

.placeholder-content {
  text-align: center;
  padding: 40px;
  background: var(--bg-card, rgba(17, 24, 39, 0.85));
  border: 1px solid var(--panel-border, rgba(0, 180, 240, 0.12));
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  max-width: 400px;
  backdrop-filter: blur(8px);
}

.placeholder-icon {
  font-size: 64px;
  margin-bottom: 20px;
  opacity: 0.6;
}

.placeholder-content h2 {
  margin: 0 0 12px 0;
  color: var(--text-primary, #dce4f0);
  font-size: 20px;
}

.placeholder-content p {
  margin: 8px 0;
  color: var(--text-secondary, #8c9ab8);
  font-size: 13px;
  line-height: 1.6;
}

.placeholder-content p.hint {
  color: var(--text-muted, #5a6880);
  font-size: 11px;
  margin-top: 16px;
}
</style>
