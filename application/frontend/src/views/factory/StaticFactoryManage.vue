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
            <span class="toolbar-title">🏗️ 北满钢铁工厂</span>
            <span class="divider">|</span>
            <span class="toolbar-label">状态: {{ isRunningTest ? "运行中..." : "就绪" }}</span>
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
            <button @click="handleExecutePlan" class="glass-btn primary" :disabled="isRunningTest"
              title="执行模拟演练">
              🚀 执行
            </button>
            <button @click="handleStop" class="glass-btn" :disabled="!isRunningTest"
              title="停止模拟">
              ⏹ 停止
            </button>
          </div>
        </div>
      </div>
    </div>

    <RightSidePanel ref="rightSidePanelRef" config-panel-title="⚙️ 静态配置" :show-chart="true"
      event-panel-title="📋 系统日志" @edit-mode-change="isEditMode = $event" />
  </div>
</template>

<script setup>
import { ref, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
import { useFactoryStore } from "@/stores/factory";
import { useMonitorStore } from "@/stores/monitor";
import { runFullSystemTest } from "@/scenarios/fullSystemTest";

import FactoryPlayerSSE from "@/components/FactoryPlayerSSE.vue";
import ControlPanel from "@/components/ControlPanel.vue";
import RightSidePanel from "@/components/RightSidePanel.vue";

const store = useFactoryStore();
const monitorStore = useMonitorStore();

const isRunningTest = ref(false);
const isEditMode = ref(false);

let stopTest = null;

// ==================== 三旋钮算法配置 ====================

const fjspOptions = [
  { label: 'PSO 粒子群', value: 'pso' },
  { label: 'DE 差分进化', value: 'de' },
  { label: 'DRL 深度强化学习', value: 'drl' },
  { label: 'BEST 最优搜索', value: 'best' },
];

const mapfOptions = [
  { label: 'A* 路由', value: 'astar' },
  { label: 'GPT 路由', value: 'mapf_gpt' },
];

const assignerOptions = [
  { label: 'FIFO 先来先服务', value: 'fifo' },
  { label: '贪心分配', value: 'greedy' },
  { label: '匈牙利算法', value: 'hungarian' },
  { label: '最小拥堵', value: 'least_congestion' },
  { label: '负载均衡', value: 'load_balance' },
  { label: '最近分配', value: 'nearest' },
  { label: '随机分配', value: 'random' },
  { label: 'SJT 最短作业', value: 'sjt' },
  { label: '紧迫度优先', value: 'urgency' },
];

const selectedFjsp = ref('pso');
const selectedMapf = ref('mapf_gpt');
const selectedAssigner = ref('nearest');

// ==================== 执行 / 停止 ====================

function handleExecutePlan() {
  if (isRunningTest.value) return;
  isRunningTest.value = true;

  const algorithm = `${selectedFjsp.value}+${selectedMapf.value}+${selectedAssigner.value}`;
  ElMessage.success(`✅ 启动模拟: ${algorithm}`);

  stopTest = runFullSystemTest(store, monitorStore, () => {
    isRunningTest.value = false;
    stopTest = null;
    ElMessage.success("✅ 模拟完成");
  });
}

function handleStop() {
  if (stopTest) {
    stopTest();
    stopTest = null;
  }
  isRunningTest.value = false;
  store.isPlaying = false;
  ElMessage.info("已停止模拟");
}

onUnmounted(() => {
  if (stopTest) { stopTest(); stopTest = null; }
  store.clearAll();
});
</script>
<style scoped>
@import "../styles/FactoryManage.css";
</style>
