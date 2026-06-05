<template>
  <div class="factory-manage-container">
    <div class="left-panel">
      <ControlPanel :disabled="isEditMode" />
    </div>
    <div class="middle-panel">
      <FactoryPlayerSSE :hide-control-panel="true" :edit-mode="isEditMode" />

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
            <select v-model="sim.selectedFjsp.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.fjspOptions.value" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <select v-model="sim.selectedMapf.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.mapfOptions.value" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <select v-model="sim.selectedAssigner.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.assignerOptions.value" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <button @click="handleExecutePlan" class="glass-btn primary"
              :disabled="sim.isRunningTest.value || isStartingContainer" title="上传选中的方案">
              🚀 上传选中方案
            </button>
          </div>
        </div>

        <!-- 容器启动加载状态 -->
        <div v-if="isStartingContainer" class="container-loading-bar">
          <div class="loading-spinner"></div>
          <span>正在启动仿真容器...</span>
          <span class="loading-detail">{{ containerStartStatus }}</span>
        </div>

        <!-- API 测试按钮组 -->
        <div class="api-test-toolbar">
          <span class="test-label">API 测试:</span>
          <button @click="testSetAlgorithm" class="test-btn" :disabled="sim.isRunningTest.value || isStartingContainer">
            1️⃣ 设定策略
          </button>
          <button @click="testReset" class="test-btn" :disabled="sim.isRunningTest.value || isStartingContainer">
            2️⃣ 重置工厂
          </button>
          <button @click="testPlay" class="test-btn" :disabled="sim.isRunningTest.value || isStartingContainer">
            3️⃣ 启动执行
          </button>
        </div>
      </div>
    </div>

    <RightSidePanel ref="rightSidePanelRef" config-panel-title="⚙️ 容器化仿真配置" :show-chart="true"
      event-panel-title="📋 系统日志" @edit-mode-change="isEditMode = $event" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
import { useFactoryStore } from "@/stores/factory";
import { useMonitorStore } from "@/stores/monitor";
import { apiPost, API_ROUTES } from "@/utils/api";
import { useSimulationConfig } from "@/composables/useSimulationConfig";

import FactoryPlayerSSE from "@/components/FactoryPlayerSSE.vue";
import ControlPanel from "@/components/ControlPanel.vue";
import RightSidePanel from "@/components/RightSidePanel.vue";

const store = useFactoryStore();
const monitorStore = useMonitorStore();

const sim = useSimulationConfig({
  defaults: { fjsp: "pso", mapf: "mapf_gpt", assigner: "nearest" },
  defaultOptions: {
    fjsp: [
      { label: "PSO 粒子群", value: "pso" },
      { label: "DE 差分进化", value: "de" },
      { label: "DRL 深度强化学习", value: "drl" },
      { label: "BEST 最优搜索", value: "best" },
    ],
    mapf: [
      { label: "A* 路由", value: "astar" },
      { label: "GPT 路由", value: "mapf_gpt" },
    ],
    assigner: [
      { label: "FIFO 先来先服务", value: "fifo" },
      { label: "贪心分配", value: "greedy" },
      { label: "匈牙利算法", value: "hungarian" },
      { label: "最小拥堵", value: "least_congestion" },
      { label: "负载均衡", value: "load_balance" },
      { label: "最近分配", value: "nearest" },
      { label: "随机分配", value: "random" },
      { label: "SJT 最短作业", value: "sjt" },
      { label: "紧迫度优先", value: "urgency" },
    ],
  },
});

// ==================== Docker 专属状态 ====================

const isEditMode = ref(false);
const isStartingContainer = ref(false);
const containerStartStatus = ref("");

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

.api-test-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: rgba(30, 30, 40, 0.85);
  border-radius: 8px;
  margin-top: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  pointer-events: auto;
}

.test-label {
  color: #a0a0a0;
  font-size: 12px;
  margin-right: 4px;
}

.test-btn {
  padding: 6px 12px;
  font-size: 12px;
  border: 1px solid rgba(100, 180, 255, 0.3);
  border-radius: 6px;
  background: rgba(60, 120, 200, 0.2);
  color: #b0d0ff;
  cursor: pointer;
  transition: all 0.2s ease;
}

.test-btn:hover:not(:disabled) {
  background: rgba(60, 120, 200, 0.4);
  border-color: rgba(100, 180, 255, 0.6);
  color: #ffffff;
}

.test-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
