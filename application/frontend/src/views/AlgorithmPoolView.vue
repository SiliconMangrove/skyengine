<template>
  <div class="algo-container">
    <div class="bg-overlay"></div>

    <div class="algo-page">
      <div class="algo-nav">
        <button class="btn-back" @click="goBack">
          <span class="icon">←</span>
          <span class="text">返回工作空间</span>
        </button>
        <h1>算法池</h1>
      </div>

      <div class="algo-content">
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
    </div>
  </div>
</template>

<script setup>
import { useRouter } from "vue-router";
import { getAllAlgorithms } from "@/config/algorithms";

const router = useRouter();
const goBack = () => router.push("/factory");

const statusLabels = { available: "可用", experimental: "实验性", coming: "即将支持" };
const { fjsp: _fjsp, mapf: _mapf, assigner: _assigner } = getAllAlgorithms();
const addStatusLabel = (list) => list.map((a) => ({ ...a, statusText: statusLabels[a.status] || "未知" }));
const fjspAlgorithms = addStatusLabel(_fjsp);
const mapfAlgorithms = addStatusLabel(_mapf);
const assignerAlgorithms = addStatusLabel(_assigner);
</script>

<style scoped>
.algo-container {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  position: relative;
  font-family: 'Inter', -apple-system, sans-serif;
  background-image: url('https://images.unsplash.com/photo-1647427060118-4911c9821b82?q=80&w=2070&auto=format&fit=crop');
  background-size: cover;
  background-position: center;
}

.bg-overlay {
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at center, rgba(15, 23, 42, 0.7) 0%, rgba(2, 6, 23, 0.95) 100%);
  z-index: 0;
}

.algo-page {
  position: relative;
  z-index: 1;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.algo-nav {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 16px 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.btn-back {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  color: #94a3b8;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-back:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #e2e8f0;
}

.algo-nav h1 {
  font-size: 18px;
  font-weight: 600;
  color: #e2e8f0;
}

.algo-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
}

.algo-content::-webkit-scrollbar {
  width: 6px;
}

.algo-content::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 3px;
}

/* Section */
.algo-section {
  margin-bottom: 36px;
}

.section-header {
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.section-tag {
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.section-tag.fjsp {
  background: rgba(99, 102, 241, 0.2);
  color: #a5b4fc;
}

.section-tag.mapf {
  background: rgba(34, 197, 94, 0.2);
  color: #86efac;
}

.section-tag.assigner {
  background: rgba(251, 146, 60, 0.2);
  color: #fdba74;
}

.section-header h2 {
  font-size: 18px;
  font-weight: 600;
  color: #e2e8f0;
}

.section-desc {
  width: 100%;
  font-size: 13px;
  color: #64748b;
  margin: 4px 0 0 0;
}

/* Cards grid */
.algo-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 14px;
}

.algo-card {
  padding: 16px 18px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  transition: all 0.2s;
}

.algo-card:hover {
  background: rgba(255, 255, 255, 0.07);
  border-color: rgba(255, 255, 255, 0.15);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.algo-name {
  font-size: 15px;
  font-weight: 600;
  color: #e2e8f0;
}

.algo-status {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

.algo-status.available {
  background: rgba(34, 197, 94, 0.15);
  color: #86efac;
}

.algo-status.experimental {
  background: rgba(251, 191, 36, 0.15);
  color: #fcd34d;
}

.algo-status.coming {
  background: rgba(148, 163, 184, 0.15);
  color: #94a3b8;
}

.algo-desc {
  font-size: 13px;
  color: #94a3b8;
  line-height: 1.6;
  margin-bottom: 12px;
}

.algo-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.meta-label {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
}

.meta-envs {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.env-tag {
  padding: 2px 8px;
  background: rgba(100, 181, 255, 0.1);
  border: 1px solid rgba(100, 181, 255, 0.2);
  border-radius: 4px;
  font-size: 11px;
  color: #64b5ff;
}
</style>
