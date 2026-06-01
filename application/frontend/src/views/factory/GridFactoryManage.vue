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
            <span class="toolbar-title">🏭 仿真工作台</span>
            <span class="divider">|</span>
            <span class="toolbar-label">状态: {{ isRunningTest ? "运行中..." : "就绪" }}</span>
            <span class="divider">|</span>
            <span class="toolbar-label connection-status" :class="connectionStatus.scenario === '已连接'
              ? 'connected'
              : 'disconnected'
              ">
              现场场景: {{ connectionStatus.scenario }}
            </span>
          </div>
          <div class="toolbar-right">
            <!-- 三旋钮: FJSP / MAPF / Assigner -->
            <select v-model="selectedFjsp" class="plan-select" :disabled="isRunningTest">
              <option v-for="opt in fjspOptions" :key="opt.value" :value="opt.value"
                :disabled="opt.disabled">
                {{ opt.label }}
              </option>
            </select>
            <select v-model="selectedMapf" class="plan-select" :disabled="isRunningTest">
              <option v-for="opt in mapfOptions" :key="opt.value" :value="opt.value"
                :disabled="opt.disabled">
                {{ opt.label }}
              </option>
            </select>
            <select v-model="selectedAssigner" class="plan-select" :disabled="isRunningTest">
              <option v-for="opt in assignerOptions" :key="opt.value" :value="opt.value"
                :disabled="opt.disabled">
                {{ opt.label }}
              </option>
            </select>
            <button @click="handleExecutePlan" class="glass-btn primary" :disabled="isRunningTest" title="上传选中的方案">
              🚀 上传选中方案
            </button>
          </div>
        </div>

        <!-- API 测试按钮组 -->
        <div class="api-test-toolbar">
          <span class="test-label">API 测试:</span>
          <button @click="testSetAlgorithm" class="test-btn" :disabled="isRunningTest" title="测试设定调度策略">
            1️⃣ 设定策略
          </button>
          <button @click="testReset" class="test-btn" :disabled="isRunningTest" title="测试重置工厂">
            2️⃣ 重置工厂
          </button>
          <button @click="testPlay" class="test-btn" :disabled="isRunningTest" title="测试启动执行">
            3️⃣ 启动执行
          </button>
        </div>
      </div>
    </div>

    <RightSidePanel ref="rightSidePanelRef" config-panel-title="⚙️ 仿真配置" :show-chart="true"
      event-panel-title="📋 系统日志" @edit-mode-change="isEditMode = $event" />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from "vue";
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

// 清理函数引用
let stopTest = null;

// ==================== 三旋钮算法配置 ====================

const fjspOptions = ref([
  { label: 'PSO 粒子群', value: 'pso', disabled: true },
  { label: 'DE 差分进化', value: 'de', disabled: true },
  { label: 'DRL 深度强化学习', value: 'drl', disabled: true },
  { label: 'BEST 最优搜索', value: 'best', disabled: false },
]);

const mapfOptions = ref([
  { label: 'A* 路由', value: 'astar', disabled: false },
  { label: 'GPT 路由', value: 'mapf_gpt', disabled: true },
]);

const assignerOptions = ref([
  { label: 'FIFO 先来先服务', value: 'fifo', disabled: true },
  { label: '贪心分配', value: 'greedy', disabled: true },
  { label: '匈牙利算法', value: 'hungarian', disabled: true },
  { label: '最小拥堵', value: 'least_congestion', disabled: true },
  { label: '负载均衡', value: 'load_balance', disabled: true },
  { label: '最近分配', value: 'nearest', disabled: true },
  { label: '随机分配', value: 'random', disabled: true },
  { label: 'SJT 最短作业', value: 'sjt', disabled: true },
  { label: '紧迫度优先', value: 'urgency', disabled: true },
]);

const selectedFjsp = ref('best');
const selectedMapf = ref('astar');
const selectedAssigner = ref('fifo');

const isRunningTest = ref(false);
const isEditMode = ref(false);
const connectionStatus = ref({
  control: "未连接",
  state: "未连接",
  metrics: "未连接",
  scenario: "未连接",
});

let eventSource = null;
let connectionManager = null;

onMounted(async () => {
  // 工厂初始化生命周期：清除上次残留状态
  console.log("✅ GridFactory 已挂载");
  store.reset();

  // 获取后端算法配置（含可用性）
  try {
    const data = await apiPost(API_ROUTES.ALGO, {});
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      if (data.fjsp?.options) fjspOptions.value = data.fjsp.options;
      if (data.mapf?.options) mapfOptions.value = data.mapf.options;
      if (data.assigner?.options) assignerOptions.value = data.assigner.options;
    }
  } catch (error) {
    console.warn("[GridFactory] 获取算法列表失败，使用默认值:", error);
  }

  // 默认选中第一个未禁用的选项
  const firstEnabled = (arr) => arr.find(o => !o.disabled);
  const fjsp = firstEnabled(fjspOptions.value);
  const mapf = firstEnabled(mapfOptions.value);
  const assigner = firstEnabled(assignerOptions.value);
  if (fjsp) selectedFjsp.value = fjsp.value;
  if (mapf) selectedMapf.value = mapf.value;
  if (assigner) selectedAssigner.value = assigner.value;
});



/**
 * 执行方案
 */
const handleExecutePlan = async () => {
  if (isRunningTest.value) return;

  const algorithm = `${selectedFjsp.value}+${selectedMapf.value}+${selectedAssigner.value}`;
  console.log(`[GridFactory] 执行方案: ${algorithm}`);

  isRunningTest.value = true;
  try {
    stopTest = await backendSystemTest(store, monitorStore, { algorithm }, () => {
      isRunningTest.value = false;
      stopTest = null;
      ElMessage.success("✅ 仿真执行完成");
    });
  } catch (error) {
    isRunningTest.value = false;
    stopTest = null;
    ElMessage.error(`仿真执行失败: ${error.message}`);
  }
};

/**
 * 测试 API 1: 设定调度策略
 */
const testSetAlgorithm = async () => {
  const algorithm = `${selectedFjsp.value}+${selectedMapf.value}+${selectedAssigner.value}`;
  console.log('[Test] Step 1: 设定调度策略...', { algorithm });
  try {
    const result = await apiPost(API_ROUTES.FACTORY_ALGORITHM_SET, { algorithm }, { timeout: 15000 });
    console.log('[Test] Step 1 完成:', result);
    ElMessage.success(`✅ 设定策略成功: ${algorithm}`);
  } catch (error) {
    console.error('[Test] Step 1 失败:', error);
    ElMessage.error(`❌ 设定策略失败: ${error.message}`);
  }
};

/**
 * 测试 API 2: 重置工厂
 */
const testReset = async () => {
  console.log('[Test] Step 2: 发送重置命令...');
  try {
    const result = await apiPost(API_ROUTES.FACTORY_CONTROL_RESET, null, { timeout: 15000 });
    console.log('[Test] Step 2 完成:', result);
    ElMessage.success('✅ 重置工厂成功');
  } catch (error) {
    console.error('[Test] Step 2 失败:', error);
    ElMessage.error(`❌ 重置工厂失败: ${error.message}`);
  }
};

/**
 * 测试 API 3: 启动执行
 */
const testPlay = async () => {
  console.log('[Test] Step 3: 发送启动命令...');
  try {
    const result = await apiPost(API_ROUTES.FACTORY_CONTROL_PLAY, null, { timeout: 15000 });
    console.log('[Test] Step 3 完成:', result);
    ElMessage.success('✅ 启动执行成功');
  } catch (error) {
    console.error('[Test] Step 3 失败:', error);
    ElMessage.error(`❌ 启动执行失败: ${error.message}`);
  }
};

onUnmounted(() => {
  // 清理连接和测试
  console.log("🛑 GridFactoryManage 卸载，清理连接和测试");
  if (stopTest) {
    stopTest();
    stopTest = null;
  }
  store.clearAll();
});
</script>
<style scoped>
@import "../styles/FactoryManage.css";

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
  /* 恢复点击事件，因为父元素 wrapper 设置了 pointer-events: none */
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
