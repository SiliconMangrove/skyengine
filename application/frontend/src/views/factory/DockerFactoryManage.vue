<template>
  <div class="factory-manage-container">
    <div class="middle-panel">
      <FactoryPlayerSSE
        :hide-control-panel="true"
        :edit-mode="isEditMode"
        :background-theme="backgroundTheme"
        :background-size="backgroundSize"
      />

      <div class="floating-toolbar-wrapper">
        <div class="floating-toolbar">
          <div class="toolbar-left">
            <span class="toolbar-title">🏭 容器化仿真工作台</span>
            <span class="divider">|</span>
            <span class="toolbar-label">状态: {{ containerStatus }}</span>
            <span class="divider">|</span>
            <span class="toolbar-label connection-status" :class="connectionStatus.scenario === '已连接'
              ? 'connected'
              : 'disconnected'
              ">
              引擎: {{ connectionStatus.scenario }}
            </span>
          </div>
          <div class="toolbar-right">
            <button @click="handleExecutePlan" class="glass-btn primary"
              :disabled="sim.isRunningTest.value || isStartingContainer" title="上传选中的方案">
              🚀 启动仿真
            </button>
          </div>
        </div>

        <!-- 容器启动加载状态 -->
        <div v-if="isStartingContainer" class="container-loading-bar">
          <div class="loading-spinner"></div>
          <span>正在启动仿真容器...</span>
          <span class="loading-detail">{{ containerStartStatus }}</span>
        </div>
      </div>
    </div>

    <FactoryTabsPanel
      :tabs="tabs"
      v-model="activeTab"
      v-model:show-panel="showPanel"
      :is-edit-mode="isEditMode"
      :is-running-test="sim.isRunningTest.value"
      @edit-mode-change="isEditMode = $event"
    >
      <template #tab-simulation>
        <div class="simulation-tab">
          <div class="sim-field">
            <label>FJSP 排程</label>
            <select v-model="sim.selectedFjsp.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.fjspOptions.value" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="sim-field">
            <label>MAPF 路由</label>
            <select v-model="sim.selectedMapf.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.mapfOptions.value" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="sim-field">
            <label>任务分配</label>
            <select v-model="sim.selectedAssigner.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.assignerOptions.value" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>

          <div class="sim-divider"></div>

          <span class="test-label">分步测试:</span>
          <button @click="testSetAlgorithm" class="step-btn" :disabled="sim.isRunningTest.value || isStartingContainer">
            1️⃣ 设定策略
          </button>
          <button @click="testReset" class="step-btn" :disabled="sim.isRunningTest.value || isStartingContainer">
            2️⃣ 重置工厂
          </button>
          <button @click="testPlay" class="step-btn" :disabled="sim.isRunningTest.value || isStartingContainer">
            3️⃣ 启动执行
          </button>

          <div class="sim-divider"></div>

          <div class="sim-field">
            <label>场景风格</label>
            <select v-model="backgroundTheme" class="plan-select">
              <option value="clean">简洁</option>
              <option value="factory">工厂车间</option>
            </select>
          </div>
          <div v-if="backgroundTheme === 'factory'" class="sim-field">
            <label>厂房尺寸</label>
            <select v-model.number="backgroundSize" class="plan-select">
              <option :value="1">紧凑</option>
              <option :value="2">标准</option>
              <option :value="3">宽敞</option>
              <option :value="4">大厅</option>
            </select>
          </div>
        </div>
      </template>
    </FactoryTabsPanel>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
import { useFactoryStore } from "@/stores/factory";
import { useMonitorStore } from "@/stores/monitor";
import { apiPost, API_ROUTES } from "@/utils/api";
import { useSimulationConfig } from "@/composables/useSimulationConfig";
import { getAlgorithmOptions } from "@/config/algorithms";

import FactoryPlayerSSE from "@/components/FactoryPlayerSSE.vue";
import FactoryTabsPanel from "@/components/FactoryTabsPanel.vue";

const store = useFactoryStore();
const monitorStore = useMonitorStore();

const sim = useSimulationConfig({
  defaults: { fjsp: "pso", mapf: "mapf_gpt", assigner: "nearest" },
  defaultOptions: {
    fjsp: getAlgorithmOptions("fjsp"),
    mapf: getAlgorithmOptions("mapf"),
    assigner: getAlgorithmOptions("assigner"),
  },
});

// ==================== Docker 专属状态 ====================

const isEditMode = ref(false);
const isStartingContainer = ref(false);
const containerStartStatus = ref("");
const showPanel = ref(true);
const activeTab = ref("simulation");
const backgroundTheme = ref("factory");
const backgroundSize = ref(2);

const tabs = [
  { key: "simulation", label: "仿真", icon: "🚀" },
  { key: "control", label: "控制", icon: "⚙️" },
  { key: "config", label: "配置", icon: "🔧" },
  { key: "agent", label: "Agent", icon: "🤖" },
  { key: "metrics", label: "指标", icon: "📊" },
  { key: "events", label: "日志", icon: "📋" },
];

const containerStatus = computed(() => {
  if (isStartingContainer.value) return '启动容器中...';
  if (sim.isRunningTest.value) return '运行中';
  return '就绪';
});

const connectionStatus = ref({
  control: "未连接",
  state: "未连接",
  metrics: "未连接",
  scenario: "未连接",
});

onMounted(async () => {
  console.log("✅ DockerFactoryManage 已挂载");
  store.reset();
  await sim.fetchAlgoOptions();
});

// ==================== 执行方案 (Docker 3步模式) ====================

const handleExecutePlan = async () => {
  if (sim.isRunningTest.value) return;

  isStartingContainer.value = true;
  containerStartStatus.value = '正在设定策略...';

  try {
    await apiPost(API_ROUTES.FACTORY_ALGORITHM_SET, {
      algorithm: sim.algorithmString.value,
    }, { timeout: 30000 });

    containerStartStatus.value = '正在重置环境...';
    await apiPost(API_ROUTES.FACTORY_CONTROL_RESET, null, { timeout: 30000 });

    containerStartStatus.value = '正在启动容器...';
    isStartingContainer.value = false;

    sim.handleExecutePlan(store, monitorStore, { mode: "docker" });
  } catch (error) {
    isStartingContainer.value = false;
    sim.isRunningTest.value = false;
    ElMessage.error(`仿真执行失败: ${error.message}`);
  }
};

// ==================== 分步测试 API ====================

const testSetAlgorithm = async () => {
  try {
    await apiPost(API_ROUTES.FACTORY_ALGORITHM_SET, {
      algorithm: sim.algorithmString.value,
    }, { timeout: 15000 });
    ElMessage.success(`✅ 设定策略成功: ${sim.algorithmString.value}`);
  } catch (error) {
    ElMessage.error(`❌ 设定策略失败: ${error.message}`);
  }
};

const testReset = async () => {
  try {
    await apiPost(API_ROUTES.FACTORY_CONTROL_RESET, null, { timeout: 15000 });
    ElMessage.success('✅ 重置工厂成功');
  } catch (error) {
    ElMessage.error(`❌ 重置工厂失败: ${error.message}`);
  }
};

const testPlay = async () => {
  try {
    await apiPost(API_ROUTES.FACTORY_CONTROL_PLAY, null, { timeout: 30000 });
    ElMessage.success('✅ 启动执行成功');
  } catch (error) {
    ElMessage.error(`❌ 启动执行失败: ${error.message}`);
  }
};

onUnmounted(() => {
  console.log("🛑 DockerFactoryManage 卸载");
  sim.cleanup(store);
});
</script>

<style scoped>
@import "../styles/FactoryManage.css";

.container-loading-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background: rgba(60, 120, 200, 0.15);
  border: 1px solid rgba(100, 180, 255, 0.3);
  border-radius: 8px;
  margin-top: 8px;
}

.loading-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(100, 180, 255, 0.3);
  border-top-color: #64b5ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-detail {
  color: #64b5ff;
  font-size: 12px;
}

.simulation-tab .step-btn {
  width: 100%;
  padding: 8px 12px;
  font-size: 13px;
  border: 1px solid rgba(100, 180, 255, 0.3);
  border-radius: 6px;
  background: rgba(60, 120, 200, 0.2);
  color: #b0d0ff;
  cursor: pointer;
  transition: all 0.2s ease;
  margin-bottom: 6px;
}

.simulation-tab .step-btn:hover:not(:disabled) {
  background: rgba(60, 120, 200, 0.4);
  border-color: rgba(100, 180, 255, 0.6);
  color: #ffffff;
}

.simulation-tab .step-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.simulation-tab .test-label {
  color: #a0a0a0;
  font-size: 12px;
  margin-bottom: 4px;
  display: block;
}

.sim-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.1);
  margin: 10px 0;
}
</style>
