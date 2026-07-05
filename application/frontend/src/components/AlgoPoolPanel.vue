<template>
  <DraggablePanel
    icon="🧠"
    title="算法池"
    :width="820"
    :height="620"
    :initial-pos="{ x: 200, y: 60 }"
    @close="ui.closeAlgoPool()"
  >
    <div class="algo-panel-content">
      <!-- FJSP -->
      <section class="algo-section">
        <div class="section-header">
          <span class="section-tag fjsp">FJSP</span>
          <h2>排程算法</h2>
          <p class="section-desc">柔性作业车间调度问题求解器，决定工件在各机器上的加工顺序与分配</p>
        </div>
        <div class="algo-cards">
          <div v-for="algo in fjspAlgorithms" :key="algo.id" class="algo-card">
            <div class="card-header">
              <span class="algo-name">{{ algo.name }}</span>
              <span class="algo-status" :class="algo.status">{{ algo.statusText }}</span>
            </div>
            <p class="algo-desc">{{ algo.description }}</p>
            <div class="algo-meta">
              <div class="meta-label">支持环境</div>
              <div class="meta-envs">
                <span v-for="env in algo.environments" :key="env" class="env-tag">{{ env }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- MAPF -->
      <section class="algo-section">
        <div class="section-header">
          <span class="section-tag mapf">MAPF</span>
          <h2>路由算法</h2>
          <p class="section-desc">多智能体路径规划求解器，为 AGV 队伍计算无冲突的最优路径</p>
        </div>
        <div class="algo-cards">
          <div v-for="algo in mapfAlgorithms" :key="algo.id" class="algo-card">
            <div class="card-header">
              <span class="algo-name">{{ algo.name }}</span>
              <span class="algo-status" :class="algo.status">{{ algo.statusText }}</span>
            </div>
            <p class="algo-desc">{{ algo.description }}</p>
            <div class="algo-meta">
              <div class="meta-label">支持环境</div>
              <div class="meta-envs">
                <span v-for="env in algo.environments" :key="env" class="env-tag">{{ env }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Assigner -->
      <section class="algo-section">
        <div class="section-header">
          <span class="section-tag assigner">Assign</span>
          <h2>分配策略</h2>
          <p class="section-desc">决定加工任务分配给哪台机器执行的策略，影响整体调度质量</p>
        </div>
        <div class="algo-cards">
          <div v-for="algo in assignerAlgorithms" :key="algo.id" class="algo-card">
            <div class="card-header">
              <span class="algo-name">{{ algo.name }}</span>
              <span class="algo-status" :class="algo.status">{{ algo.statusText }}</span>
            </div>
            <p class="algo-desc">{{ algo.description }}</p>
            <div class="algo-meta">
              <div class="meta-label">支持环境</div>
              <div class="meta-envs">
                <span v-for="env in algo.environments" :key="env" class="env-tag">{{ env }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </DraggablePanel>
</template>

<script setup>
import { getAllAlgorithms } from '@/config/algorithms'
import DraggablePanel from './DraggablePanel.vue'
import { useUiPanelStore } from '@/stores/uiPanel'

const ui = useUiPanelStore()

const statusLabels = { available: '可用', experimental: '实验性', coming: '即将支持' }
const { fjsp: _fjsp, mapf: _mapf, assigner: _assigner } = getAllAlgorithms()
const addStatusLabel = (list) =>
  list.map((a) => ({ ...a, statusText: statusLabels[a.status] || '未知' }))
const fjspAlgorithms = addStatusLabel(_fjsp)
const mapfAlgorithms = addStatusLabel(_mapf)
const assignerAlgorithms = addStatusLabel(_assigner)
</script>

<style scoped>
.algo-panel-content {
  height: 100%;
  overflow-y: auto;
  padding: 16px 20px;
  font-family: 'Inter', -apple-system, sans-serif;
}
.algo-panel-content::-webkit-scrollbar { width: 6px; }
.algo-panel-content::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 3px;
}

/* Section */
.algo-section { margin-bottom: 24px; }
.section-header {
  margin-bottom: 12px;
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.section-tag {
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.section-tag.fjsp { background: rgba(99, 102, 241, 0.2); color: #a5b4fc; }
.section-tag.mapf { background: rgba(34, 197, 94, 0.2); color: #86efac; }
.section-tag.assigner { background: rgba(251, 146, 60, 0.2); color: #fdba74; }

.section-header h2 {
  font-size: 16px;
  font-weight: 600;
  color: #e2e8f0;
  margin: 0;
}
.section-desc {
  width: 100%;
  font-size: 12px;
  color: #64748b;
  margin: 4px 0 0 0;
}

/* Cards grid */
.algo-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}
.algo-card {
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  transition: all 0.2s;
}
.algo-card:hover {
  background: rgba(255, 255, 255, 0.07);
  border-color: rgba(255, 255, 255, 0.15);
}
.card-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 6px;
}
.algo-name {
  font-size: 14px; font-weight: 600; color: #e2e8f0;
}
.algo-status {
  padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 500;
}
.algo-status.available { background: rgba(34, 197, 94, 0.15); color: #86efac; }
.algo-status.experimental { background: rgba(251, 191, 36, 0.15); color: #fcd34d; }
.algo-status.coming { background: rgba(148, 163, 184, 0.15); color: #94a3b8; }

.algo-desc {
  font-size: 12px; color: #94a3b8; line-height: 1.5; margin-bottom: 8px;
}
.algo-meta { display: flex; align-items: center; gap: 8px; }
.meta-label { font-size: 10px; color: #64748b; white-space: nowrap; }
.meta-envs { display: flex; gap: 5px; flex-wrap: wrap; }
.env-tag {
  padding: 1px 6px;
  background: rgba(100, 181, 255, 0.1);
  border: 1px solid rgba(100, 181, 255, 0.2);
  border-radius: 3px;
  font-size: 10px;
  color: #64b5ff;
}
</style>
