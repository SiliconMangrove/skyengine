<template>
  <div class="factory-manage-container">
    <div class="left-panel">
      <ControlPanel />
    </div>
    <div class="middle-panel">
      <FactoryPlayerSSE :hide-control-panel="true" />

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
            <!-- 三旋钮: FJSP / MAPF / Assigner -->
            <select v-model="selectedFjsp" class="plan-select" :disabled="isRunningTest">
              <option v-for="opt in fjspOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <select v-model="selectedMapf" class="plan-select" :disabled="isRunningTest">
              <option v-for="opt in mapfOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <select v-model="selectedAssigner" class="plan-select" :disabled="isRunningTest">
              <option v-for="opt in assignerOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <button @click="handleExecutePlan" class="glass-btn primary"
              :disabled="isRunningTest || isStartingContainer" title="上传选中的方案">
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
          <button @click="testSetAlgorithm" class="test-btn" :disabled="isRunningTest || isStartingContainer">
            1️⃣ 设定策略
          </button>
          <button @click="testReset" class="test-btn" :disabled="isRunningTest || isStartingContainer">
            2️⃣ 重置工厂
          </button>
          <button @click="testPlay" class="test-btn" :disabled="isRunningTest || isStartingContainer">
            3️⃣ 启动执行
          </button>
        </div>
      </div>
    </div>

    <RightSidePanel ref="rightSidePanelRef" config-panel-title="⚙️ 容器化仿真配置" :show-chart="true"
      event-panel-title="📋 系统日志" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
import { useFactoryStore } from "@/stores/factory";
import { useMonitorStore } from "@/stores/monitor";
import { backendSystemTest } from "@/scenarios/backendSystemTest";
import { apiPost, API_ROUTES } from "@/utils/api";

import FactoryPlayerSSE from "@/components/FactoryPlayerSSE.vue";
import ControlPanel from "@/components/ControlPanel.vue";
import RightSidePanel from "@/components/RightSidePanel.vue";

const store = useFactoryStore();
const monitorStore = useMonitorStore();

let stopTest = null;

// ==================== 三旋钮算法配置 ====================
// 带 needs_image 和 available 标记的下拉选项

const fjspOptions = ref([
  { label: 'PSO 粒子群', value: 'pso' },
  { label: 'DE 差分进化', value: 'de' },
  { label: 'DRL 深度强化学习', value: 'drl' },
  { label: 'BEST 最优搜索', value: 'best' },
]);

const mapfOptions = ref([
  { label: 'A* 路由', value: 'astar' },
  { label: 'GPT 路由', value: 'mapf_gpt' },
]);

const assignerOptions = ref([
  { label: 'FIFO 先来先服务', value: 'fifo' },
  { label: '贪心分配', value: 'greedy' },
  { label: '匈牙利算法', value: 'hungarian' },
  { label: '最小拥堵', value: 'least_congestion' },
  { label: '负载均衡', value: 'load_balance' },
  { label: '最近分配', value: 'nearest' },
  { label: '随机分配', value: 'random' },
  { label: 'SJT 最短作业', value: 'sjt' },
  { label: '紧迫度优先', value: 'urgency' },
]);

const selectedFjsp = ref('pso');
const selectedMapf = ref('mapf_gpt');
const selectedAssigner = ref('nearest');

// ==================== 状态 ====================

const isRunningTest = ref(false);
const isStartingContainer = ref(false);
const containerStartStatus = ref('');

const containerStatus = computed(() => {
  if (isStartingContainer.value) return '启动容器中...';
  if (isRunningTest.value) return '运行中';
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

  // 获取后端算法配置（含镜像可用性）
  try {
    const data = await apiPost(API_ROUTES.ALGO, {});
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      // 后端返回带 available 标记的选项
      if (data.fjsp?.options) fjspOptions.value = data.fjsp.options;
      if (data.mapf?.options) mapfOptions.value = data.mapf.options;
      if (data.assigner?.options) assignerOptions.value = data.assigner.options;
    }
  } catch (error) {
    console.warn("[DockerFactory] 获取算法列表失败，使用默认值:", error);
  }

  // 默认选中第一个选项
  if (fjspOptions.value.length > 0) selectedFjsp.value = fjspOptions.value[0].value;
  if (mapfOptions.value.length > 0) selectedMapf.value = mapfOptions.value[0].value;
  if (assignerOptions.value.length > 0) selectedAssigner.value = assignerOptions.value[0].value;
});

// ==================== 执行方案 ====================

const handleExecutePlan = async () => {
  if (isRunningTest.value) return;

  const algorithm = `${selectedFjsp.value}+${selectedMapf.value}+${selectedAssigner.value}`;
  console.log(`[DockerFactory] 执行方案: ${algorithm}`);

  isStartingContainer.value = true;
  containerStartStatus.value = '正在设定策略...';

  try {
    // Step 1: 设定算法 (后端期望 {"algorithm": "fjsp+mapf+assigner"} 格式)
    await apiPost(API_ROUTES.FACTORY_ALGORITHM_SET, {
      algorithm: algorithm,
    }, { timeout: 30000 });

    containerStartStatus.value = '正在重置环境...';

    // Step 2: 重置
    await apiPost(API_ROUTES.FACTORY_CONTROL_RESET, null, { timeout: 30000 });

    containerStartStatus.value = '正在启动容器...';

    // Step 3: 启动（DockerProxy.start() 会启动容器）
    isStartingContainer.value = false;
    isRunningTest.value = true;

    stopTest = await backendSystemTest(store, monitorStore, { algorithm }, () => {
      isRunningTest.value = false;
      stopTest = null;
      ElMessage.success("✅ 容器化仿真执行完成");
    });
  } catch (error) {
    isStartingContainer.value = false;
    isRunningTest.value = false;
    stopTest = null;
    ElMessage.error(`仿真执行失败: ${error.message}`);
  }
};

// ==================== 分步测试 API ====================

const testSetAlgorithm = async () => {
  const algorithm = `${selectedFjsp.value}+${selectedMapf.value}+${selectedAssigner.value}`;
  try {
    const result = await apiPost(API_ROUTES.FACTORY_ALGORITHM_SET, {
      algorithm: algorithm,
    }, { timeout: 15000 });
    ElMessage.success(`✅ 设定策略成功: ${algorithm}`);
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
  if (stopTest) {
    stopTest();
    stopTest = null;
  }
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
