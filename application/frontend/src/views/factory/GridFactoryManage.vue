<template>
  <div class="factory-manage-container">
    <div class="middle-panel">
      <FactoryPlayerSSE
        :hide-control-panel="true"
        :edit-mode="isEditMode"
        :background-theme="backgroundTheme"
        :background-size="backgroundSize"
      />
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
              <option v-for="opt in sim.fjspOptions.value" :key="opt.value" :value="opt.value" :disabled="opt.disabled">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="sim-field">
            <label>MAPF 路由</label>
            <select v-model="sim.selectedMapf.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.mapfOptions.value" :key="opt.value" :value="opt.value" :disabled="opt.disabled">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="sim-field">
            <label>任务分配</label>
            <select v-model="sim.selectedAssigner.value" class="plan-select" :disabled="sim.isRunningTest.value">
              <option v-for="opt in sim.assignerOptions.value" :key="opt.value" :value="opt.value" :disabled="opt.disabled">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <button @click="handleExecute" class="launch-btn" :disabled="sim.isRunningTest.value">
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
      </template>
    </FactoryTabsPanel>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from "vue";
import { useFactoryStore } from "@/stores/factory";
import { useMonitorStore } from "@/stores/monitor";
import { useSimulationConfig } from "@/composables/useSimulationConfig";
import FactoryPlayerSSE from "@/components/FactoryPlayerSSE.vue";
import FactoryTabsPanel from "@/components/FactoryTabsPanel.vue";

const store = useFactoryStore();
const monitorStore = useMonitorStore();

const sim = useSimulationConfig({
  defaults: { fjsp: "greedy", mapf: "astar", assigner: "nearest" },
  defaultOptions: {
    fjsp: [
      { label: "PSO 粒子群", value: "pso", disabled: true },
      { label: "DE 差分进化", value: "de", disabled: true },
      { label: "DRL 深度强化学习", value: "drl", disabled: true },
      { label: "BEST 最优搜索", value: "best", disabled: true },
      { label: "Greedy 贪心搜索", value: "greedy", disabled: false },
    ],
    mapf: [
      { label: "A* 路由", value: "astar", disabled: false },
      { label: "GPT 路由", value: "mapf_gpt", disabled: true },
    ],
    assigner: [
      { label: "贪心分配", value: "greedy", disabled: true },
      { label: "最小拥堵", value: "least_congestion", disabled: true },
      { label: "负载均衡", value: "load_balance", disabled: true },
      { label: "最近分配", value: "nearest", disabled: false },
      { label: "随机分配", value: "random", disabled: true },
    ],
  },
});

const isEditMode = ref(false);
const showPanel = ref(true);
const activeTab = ref("simulation");
const backgroundTheme = ref("factory");
const backgroundSize = ref(2);

const tabs = [
  { key: "simulation", label: "仿真", icon: "🚀" },
  { key: "control", label: "控制", icon: "⚙️" },
  { key: "config", label: "配置", icon: "🔧" },
  { key: "agent", label: "分析", icon: "🤖" },
  { key: "metrics", label: "指标", icon: "📊" },
  { key: "events", label: "日志", icon: "📋" },
];

onMounted(async () => {
  console.log("[GridFactory] 已挂载");
  store.reset();
  await sim.fetchAlgoOptions();
});

function handleExecute() {
  sim.handleExecutePlan(store, monitorStore, { mode: "backend" });
}

onUnmounted(() => {
  console.log("[GridFactory] 卸载，清理连接和测试");
  sim.cleanup(store);
});
</script>

<style scoped>
@import "../styles/FactoryManage.css";
</style>
