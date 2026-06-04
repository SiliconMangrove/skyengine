<template>
  <div class="factory-manage-container">
    <!-- 工厂可视化 (全屏) -->
    <div class="middle-panel">
      <FactoryPlayerSSE :hide-control-panel="true" :edit-mode="isEditMode" :background-theme="backgroundTheme" :background-size="backgroundSize" />
    </div>

    <!-- 唯一浮动面板: Tab 切换 -->
    <DraggablePanel
      v-if="showPanel"
      :title="currentTab.label"
      :icon="currentTab.icon"
      :width="300"
      :initial-pos="{ x: 12, y: 12 }"
      :max-height="0"
      @close="showPanel = false"
    >
      <!-- Tab 栏 -->
      <div class="dp-tabs">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="dp-tab-btn"
          :class="{ active: activeTab === tab.key }"
          @click="activeTab = tab.key"
        >
          {{ tab.icon }} {{ tab.label }}
        </button>
      </div>

      <!-- 仿真 Tab: 算法选择 + 启动 -->
      <div v-if="activeTab === 'simulation'" class="simulation-tab">
        <div class="sim-field">
          <label>FJSP 排程</label>
          <select v-model="selectedFjsp" class="plan-select" :disabled="isRunningTest">
            <option v-for="opt in fjspOptions" :key="opt.value" :value="opt.value" :disabled="opt.disabled">
              {{ opt.label }}
            </option>
          </select>
        </div>
        <div class="sim-field">
          <label>MAPF 路由</label>
          <select v-model="selectedMapf" class="plan-select" :disabled="isRunningTest">
            <option v-for="opt in mapfOptions" :key="opt.value" :value="opt.value" :disabled="opt.disabled">
              {{ opt.label }}
            </option>
          </select>
        </div>
        <div class="sim-field">
          <label>任务分配</label>
          <select v-model="selectedAssigner" class="plan-select" :disabled="isRunningTest">
            <option v-for="opt in assignerOptions" :key="opt.value" :value="opt.value" :disabled="opt.disabled">
              {{ opt.label }}
            </option>
          </select>
        </div>
        <button @click="handleExecutePlan" class="launch-btn" :disabled="isRunningTest">
          🚀 启动仿真
        </button>

        <div class="sim-field" style="margin-top: 4px">
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

      <!-- 控制 Tab -->
      <ControlPanel v-if="activeTab === 'control'" :disabled="isEditMode" />

      <!-- 配置 Tab -->
      <ConfigPanel
        v-if="activeTab === 'config'"
        ref="configPanelRef"
        @edit-mode-change="isEditMode = $event"
      />

      <!-- 指标 Tab -->
      <MetricsPanel v-if="activeTab === 'metrics'" :show-chart="true" />

      <!-- 日志 Tab -->
      <EventPanel v-if="activeTab === 'events'" title="系统日志" />
    </DraggablePanel>

    <!-- 边缘切换按钮 -->
    <button v-if="!showPanel" class="panel-toggle left" @click="showPanel = true">
      ▶ 面板
    </button>
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
import ConfigPanel from "@/components/ConfigPanel.vue";
import MetricsPanel from "@/components/MetricsPanel.vue";
import EventPanel from "@/components/EventPanel.vue";
import DraggablePanel from "@/components/DraggablePanel.vue";

const store = useFactoryStore();
const monitorStore = useMonitorStore();

// ==================== 面板状态 ====================

const showPanel = ref(true);
const activeTab = ref("simulation");

const tabs = [
  { key: "simulation", label: "仿真", icon: "🚀" },
  { key: "control", label: "控制", icon: "⚙️" },
  { key: "config", label: "配置", icon: "🔧" },
  { key: "metrics", label: "指标", icon: "📊" },
  { key: "events", label: "日志", icon: "📋" },
];

const currentTab = computed(() => tabs.find((t) => t.key === activeTab.value) || tabs[0]);

const isEditMode = ref(false);
const isRunningTest = ref(false);
const backgroundTheme = ref("clean");
const backgroundSize = ref(2);

// ==================== 三旋钮算法配置 ====================

const fjspOptions = ref([
  { label: "PSO 粒子群", value: "pso", disabled: true },
  { label: "DE 差分进化", value: "de", disabled: true },
  { label: "DRL 深度强化学习", value: "drl", disabled: true },
  { label: "BEST 最优搜索", value: "best", disabled: false },
]);

const mapfOptions = ref([
  { label: "A* 路由", value: "astar", disabled: false },
  { label: "GPT 路由", value: "mapf_gpt", disabled: true },
]);

const assignerOptions = ref([
  { label: "FIFO 先来先服务", value: "fifo", disabled: true },
  { label: "贪心分配", value: "greedy", disabled: true },
  { label: "匈牙利算法", value: "hungarian", disabled: true },
  { label: "最小拥堵", value: "least_congestion", disabled: true },
  { label: "负载均衡", value: "load_balance", disabled: true },
  { label: "最近分配", value: "nearest", disabled: true },
  { label: "随机分配", value: "random", disabled: true },
  { label: "SJT 最短作业", value: "sjt", disabled: true },
  { label: "紧迫度优先", value: "urgency", disabled: true },
]);

const selectedFjsp = ref("best");
const selectedMapf = ref("astar");
const selectedAssigner = ref("fifo");

// ==================== 生命周期 ====================

let stopTest = null;

onMounted(async () => {
  console.log("[GridFactory] 已挂载");
  store.reset();

  try {
    const data = await apiPost(API_ROUTES.ALGO, {});
    if (data && typeof data === "object" && !Array.isArray(data)) {
      if (data.fjsp?.options) fjspOptions.value = data.fjsp.options;
      if (data.mapf?.options) mapfOptions.value = data.mapf.options;
      if (data.assigner?.options) assignerOptions.value = data.assigner.options;
    }
  } catch (error) {
    console.warn("[GridFactory] 获取算法列表失败，使用默认值:", error);
  }

  const firstEnabled = (arr) => arr.find((o) => !o.disabled);
  const fjsp = firstEnabled(fjspOptions.value);
  const mapf = firstEnabled(mapfOptions.value);
  const assigner = firstEnabled(assignerOptions.value);
  if (fjsp) selectedFjsp.value = fjsp.value;
  if (mapf) selectedMapf.value = mapf.value;
  if (assigner) selectedAssigner.value = assigner.value;
});

// ==================== 执行方案 ====================

const SIMULATION_TIMEOUT_MS = 30_000;
let simulationTimeout = null;

const handleExecutePlan = async () => {
  if (isRunningTest.value) return;

  const algorithm = `${selectedFjsp.value}+${selectedMapf.value}+${selectedAssigner.value}`;
  console.log(`[GridFactory] 执行方案: ${algorithm}`);

  isRunningTest.value = true;

  simulationTimeout = setTimeout(() => {
    if (isRunningTest.value) {
      ElMessage.warning("仿真启动超时，请检查后端服务是否正常运行");
    }
  }, SIMULATION_TIMEOUT_MS);

  try {
    stopTest = await backendSystemTest(store, monitorStore, { algorithm }, () => {
      clearTimeout(simulationTimeout);
      simulationTimeout = null;
      isRunningTest.value = false;
      stopTest = null;
      ElMessage.success("仿真执行完成");
    });
  } catch (error) {
    clearTimeout(simulationTimeout);
    simulationTimeout = null;
    isRunningTest.value = false;
    stopTest = null;
    ElMessage.error(`仿真执行失败: ${error.message}`);
  }
};

onUnmounted(() => {
  console.log("[GridFactory] 卸载，清理连接和测试");
  clearTimeout(simulationTimeout);
  simulationTimeout = null;
  if (stopTest) {
    stopTest();
    stopTest = null;
  }
  store.clearAll();
});
</script>

<style scoped>
@import "../styles/FactoryManage.css";

/* ==================== Tab 栏 ==================== */

.dp-tabs {
  display: flex;
  border-bottom: 1px solid rgba(100, 180, 255, 0.1);
  padding: 0 4px;
  flex-shrink: 0;
}

.dp-tab-btn {
  flex: 1;
  padding: 7px 0;
  border: none;
  background: transparent;
  color: rgba(160, 190, 230, 0.5);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  border-bottom: 2px solid transparent;
  white-space: nowrap;
}

.dp-tab-btn:hover {
  color: rgba(200, 220, 255, 0.8);
}

.dp-tab-btn.active {
  color: rgba(200, 220, 255, 0.95);
  border-bottom-color: rgba(100, 180, 255, 0.6);
}

/* ==================== 仿真 Tab 内容 ==================== */

.simulation-tab {
  padding: 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sim-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sim-field label {
  font-size: 11px;
  color: rgba(160, 190, 230, 0.6);
  font-weight: 500;
}

.plan-select {
  padding: 7px 10px;
  border: 1px solid rgba(100, 180, 255, 0.15);
  background: rgba(20, 25, 45, 0.6);
  border-radius: 6px;
  cursor: pointer;
  color: rgba(200, 220, 255, 0.9);
  font-size: 12px;
  outline: none;
  transition: border-color 0.2s;
}

.plan-select option {
  background: #141929;
  color: rgba(200, 220, 255, 0.9);
}

.plan-select:hover:not(:disabled) {
  border-color: rgba(100, 180, 255, 0.35);
}

.plan-select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.launch-btn {
  margin-top: 4px;
  padding: 9px 0;
  border: 1px solid rgba(102, 126, 234, 0.3);
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.5) 0%, rgba(118, 75, 162, 0.5) 100%);
  border-radius: 8px;
  cursor: pointer;
  font-weight: 600;
  color: rgba(200, 220, 255, 0.95);
  font-size: 13px;
  transition: all 0.2s;
}

.launch-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.7) 0%, rgba(118, 75, 162, 0.7) 100%);
  box-shadow: 0 0 16px rgba(102, 126, 234, 0.25);
}

.launch-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
