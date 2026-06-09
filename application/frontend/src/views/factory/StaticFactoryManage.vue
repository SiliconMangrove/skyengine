<template>
  <div class="factory-manage-container">
    <div class="middle-panel">
      <FactoryPlayerSSE :hide-control-panel="true" :edit-mode="isEditMode" />
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
          <div class="sim-btn-row">
            <button @click="handleExecute" class="launch-btn" :disabled="sim.isRunningTest.value">
              🚀 执行
            </button>
            <button @click="sim.handleStop(store)" class="stop-btn" :disabled="!sim.isRunningTest.value">
              ⏹ 停止
            </button>
          </div>
        </div>
      </template>
    </FactoryTabsPanel>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from "vue";
import { useFactoryStore } from "@/stores/factory";
import { useMonitorStore } from "@/stores/monitor";
import { useSimulationConfig } from "@/composables/useSimulationConfig";
import FactoryPlayerSSE from "@/components/FactoryPlayerSSE.vue";
import FactoryTabsPanel from "@/components/FactoryTabsPanel.vue";

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

const isEditMode = ref(false);
const showPanel = ref(true);
const activeTab = ref("simulation");

const tabs = [
  { key: "simulation", label: "仿真", icon: "🚀" },
  { key: "control", label: "控制", icon: "⚙️" },
  { key: "config", label: "配置", icon: "🔧" },
  { key: "agent", label: "分析", icon: "🤖" },
  { key: "metrics", label: "指标", icon: "📊" },
  { key: "events", label: "日志", icon: "📋" },
];

function handleExecute() {
  sim.handleExecutePlan(store, monitorStore, { mode: "frontend" });
}

onUnmounted(() => sim.cleanup(store));
</script>

<style scoped>
@import "../styles/FactoryManage.css";
</style>
