<template>
  <div class="agent-panel">
    <!-- ===== 顶部 Agent 状态概览 ===== -->
    <div class="agent-header">
      <h3>🤖 异常分析检测 </h3>
      <span class="agent-counter" v-if="agvList.length > 0">
        {{ activeCount }}/{{ agvList.length }} 活跃
      </span>
    </div>

    <!-- Agent 列表 -->
    <div v-if="agvList.length === 0" class="agent-empty">
      <span>暂无 Agent 数据</span>
    </div>
    <div v-else class="agent-list">
      <div
        v-for="agv in agvList"
        :key="agv.id"
        class="agent-card"
        :class="{ active: agv._active, idle: !agv._active }"
        @click="selectedAgvId = selectedAgvId === agv.id ? null : agv.id"
      >
        <div class="agent-card-header">
          <span class="agent-icon">🤖</span>
          <span class="agent-name">{{ agv.name || `AGV-${agv.id}` }}</span>
          <span class="agent-status-dot" :class="agv._active ? 'dot-active' : 'dot-idle'"></span>
        </div>
        <div class="agent-card-body">
          <div class="agent-field">
            <span class="agent-field-label">位置</span>
            <span class="agent-field-value">
              {{ agv._pos ? `(${agv._pos[0]}, ${agv._pos[1]})` : '--' }}
            </span>
          </div>
          <div class="agent-field">
            <span class="agent-field-label">速度</span>
            <span class="agent-field-value">{{ agv.velocity ?? '--' }}</span>
          </div>
          <div class="agent-field">
            <span class="agent-field-label">容量</span>
            <span class="agent-field-value">{{ agv.capacity ?? '--' }}</span>
          </div>
          <div class="agent-field">
            <span class="agent-field-label">状态</span>
            <span class="agent-field-value status-tag" :class="agv._active ? 'status-active' : 'status-idle'">
              {{ agv._active ? '运行中' : (agv.status || '空闲') }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 汇总条 -->
    <div class="agent-summary" v-if="agvList.length > 0">
      <div class="summary-item">
        <span class="summary-label">总 Agent</span>
        <span class="summary-value">{{ agvList.length }}</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">活跃</span>
        <span class="summary-value active-text">{{ activeCount }}</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">空闲</span>
        <span class="summary-value idle-text">{{ agvList.length - activeCount }}</span>
      </div>
    </div>

    <!-- ===== 分析区域 ===== -->
    <div class="analysis-section">
      <div class="analysis-header">
        <span class="analysis-title">💡 智能分析</span>
        <button class="clear-analysis-btn" v-if="analysisResult" @click="analysisResult = ''" title="清除">×</button>
      </div>

      <!-- 预设问题模板 -->
      <div class="template-list">
        <button
          v-for="tpl in templates"
          :key="tpl.id"
          class="template-btn"
          :class="{ active: selectedTemplate === tpl.id }"
          @click="handleTemplateClick(tpl)"
          :disabled="tpl.disabled"
          :title="tpl.disabled ? '即将上线' : tpl.prompt"
        >
          <span class="tpl-icon">{{ tpl.icon }}</span>
          <span class="tpl-label">{{ tpl.label }}</span>
        </button>
      </div>

      <!-- 自定义输入 -->
      <div class="analysis-input-row">
        <input
          v-model="customQuery"
          class="analysis-input"
          placeholder="输入分析问题..."
          @keyup.enter="handleCustomQuery"
        />
        <button class="send-btn" @click="handleCustomQuery" :disabled="!customQuery.trim()">
          ▶
        </button>
      </div>

      <!-- 分析结果展示框 -->
      <div class="analysis-result-box" :class="{ 'has-content': !!analysisResult }">
        <div v-if="!analysisResult" class="result-placeholder">
          <span>选择上方模板或输入问题，分析结果将在此展示</span>
        </div>
        <div v-else class="result-content" v-html="renderedResult"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useFactoryStore } from '@/stores/factory'
import { useMonitorStore } from '@/stores/monitor'

const store = useFactoryStore()
const monitorStore = useMonitorStore()

// ==================== Agent 状态 ====================

const agvList = computed(() => {
  const configAgvs = store.getAGVs()
  const snapshot = store.currentState
  const positions = snapshot?.grid_state?.positions_xy ?? []
  const isActive = snapshot?.grid_state?.is_active ?? []

  return configAgvs.map((agv, i) => ({
    ...agv,
    _pos: positions[i] ?? agv.initialLocation ?? null,
    _active: isActive[i] ?? false,
  }))
})

const activeCount = computed(() => agvList.value.filter(a => a._active).length)
const selectedAgvId = ref(null)

// ==================== 分析模板 ====================

const templates = [
  {
    id: 'efficiency',
    icon: '⚡',
    label: 'AGV 效率分析',
    prompt: '分析当前 AGV 的整体工作效率，包括活跃率、平均负载和空驶情况',
    disabled: false,
  },
  {
    id: 'bottleneck',
    icon: '🔍',
    label: '瓶颈检测',
    prompt: '检测当前生产线上可能的瓶颈节点（机器拥堵、AGV 堆叠等）',
    disabled: false,
  },
  {
    id: 'machine_load',
    icon: '📊',
    label: '机器负载均衡',
    prompt: '分析各机器的工作负载分布，判断是否存在负载不均衡问题',
    disabled: false,
  },
  {
    id: 'transfer_analysis',
    icon: '🚛',
    label: '运输路径分析',
    prompt: '分析当前活跃运输任务的路径分布和 AGV 调度合理性',
    disabled: false,
  },
  {
    id: 'event_summary',
    icon: '📋',
    label: '日志事件摘要',
    prompt: '对近期系统日志进行分类汇总，提取关键异常和趋势',
    disabled: false,
  },
  {
    id: 'optimization',
    icon: '🧠',
    label: '优化建议',
    prompt: '基于当前运行状态，给出调度参数和工厂配置的优化建议',
    disabled: true,
  },
]

const selectedTemplate = ref(null)
const customQuery = ref('')
const analysisResult = ref('')

// ==================== 分析逻辑 ====================

function handleTemplateClick(tpl) {
  if (tpl.disabled) return
  selectedTemplate.value = tpl.id
  runAnalysis(tpl.id, tpl.prompt)
}

function handleCustomQuery() {
  const q = customQuery.value.trim()
  if (!q) return
  selectedTemplate.value = null
  runAnalysis('custom', q)
}

function runAnalysis(type, prompt) {
  // 根据模板类型生成基于当前数据的分析结果
  let result = ''

  switch (type) {
    case 'efficiency':
      result = buildEfficiencyAnalysis()
      break
    case 'bottleneck':
      result = buildBottleneckAnalysis()
      break
    case 'machine_load':
      result = buildMachineLoadAnalysis()
      break
    case 'transfer_analysis':
      result = buildTransferAnalysis()
      break
    case 'event_summary':
      result = buildEventSummary()
      break
    case 'custom':
      result = buildCustomAnalysis(prompt)
      break
  }

  analysisResult.value = result
}

// --- 分析生成器 ---

function buildEfficiencyAnalysis() {
  const total = agvList.value.length
  const active = activeCount.value
  const rate = total > 0 ? ((active / total) * 100).toFixed(1) : 0
  const machines = store.currentState?.machines || {}
  const machineList = Object.values(machines)
  const workingMachines = machineList.filter(m => m.status === 'WORKING').length

  const rating = rate >= 80 ? '🟢 优秀' : rate >= 50 ? '🟡 一般' : '🔴 较低'

  return [
    `<strong>AGV 效率分析报告</strong>`,
    ``,
    `<strong>活跃率:</strong> ${rate}% (${active}/${total}) — ${rating}`,
    `<strong>机器配合:</strong> ${workingMachines} 台机器正在工作`,
    `<strong>时间步:</strong> T+${store.currentState?.env_timeline || 0}`,
    ``,
    buildRecommendation(rate),
  ].join('\n')
}

function buildBottleneckAnalysis() {
  const machines = store.currentState?.machines || {}
  const transfers = store.currentState?.active_transfers || []
  const machineList = Object.entries(machines)
  const workingMachines = machineList.filter(([, m]) => m.status === 'WORKING')
  const idleMachines = machineList.filter(([, m]) => m.status === 'IDLE')

  const lines = [
    `<strong>瓶颈检测报告</strong>`,
    ``,
    `<strong>机器状态:</strong> ${workingMachines.length} 工作 / ${idleMachines.length} 空闲 / ${machineList.length} 总计`,
    `<strong>活跃运输:</strong> ${transfers.length} 个任务`,
  ]

  // 检测同位置 AGV 堆叠
  const positions = store.currentState?.grid_state?.positions_xy ?? []
  const posMap = {}
  positions.forEach((pos, i) => {
    const key = `${pos[0]},${pos[1]}`
    if (!posMap[key]) posMap[key] = []
    posMap[key].push(i)
  })
  const collisions = Object.entries(posMap).filter(([, ids]) => ids.length > 1)
  if (collisions.length > 0) {
    lines.push(``)
    lines.push(`<span class="tag-warn">⚠ 检测到 ${collisions.length} 处 AGV 位置重叠:</span>`)
    collisions.forEach(([pos, ids]) => {
      lines.push(`  (${pos}) — AGV: ${ids.join(', ')}`)
    })
  }

  if (workingMachines.length > 0 && transfers.length === 0) {
    lines.push(``)
    lines.push(`<span class="tag-warn">⚠ 机器工作中但无运输任务，可能存在物流断流</span>`)
  }

  if (collisions.length === 0 && (workingMachines.length === 0 || transfers.length > 0)) {
    lines.push(``)
    lines.push(`<span class="tag-ok">✓ 未检测到明显瓶颈</span>`)
  }

  return lines.join('\n')
}

function buildMachineLoadAnalysis() {
  const machines = store.currentState?.machines || {}
  const machineList = Object.entries(machines)

  if (machineList.length === 0) {
    return [`<strong>机器负载分析</strong>`, ``, `暂无机器数据，请加载工厂配置并启动仿真`].join('\n')
  }

  const working = machineList.filter(([, m]) => m.status === 'WORKING').length
  const idle = machineList.filter(([, m]) => m.status === 'IDLE').length
  const loadRate = ((working / machineList.length) * 100).toFixed(1)

  const lines = [
    `<strong>机器负载均衡报告</strong>`,
    ``,
    `<strong>总机器:</strong> ${machineList.length}`,
    `<strong>负载率:</strong> ${loadRate}% (${working} 工作 / ${idle} 空闲)`,
    ``,
    `<strong>各机器状态:</strong>`,
  ]

  machineList.forEach(([key, m]) => {
    const statusIcon = m.status === 'WORKING' ? '🔵' : '⚪'
    const progress = m.progress != null ? ` (${m.progress}%)` : ''
    lines.push(`  ${statusIcon} ${m.name || key}: ${m.status}${progress}`)
  })

  const balance = loadRate >= 70 ? '🟢 负载较均衡' : loadRate >= 40 ? '🟡 部分机器空闲' : '🔴 负载偏低'
  lines.push('')
  lines.push(`<strong>评估:</strong> ${balance}`)

  return lines.join('\n')
}

function buildTransferAnalysis() {
  const transfers = store.currentState?.active_transfers || []
  const timeline = store.currentState?.env_timeline || '0'

  const lines = [
    `<strong>运输路径分析</strong>`,
    ``,
    `<strong>当前时间:</strong> T+${timeline}`,
    `<strong>活跃运输:</strong> ${transfers.length} 个`,
  ]

  if (transfers.length === 0) {
    lines.push('')
    lines.push('当前无活跃运输任务')
    if (activeCount.value > 0) {
      lines.push(`<span class="tag-warn">⚠ ${activeCount.value} 台 AGV 活跃但无分配任务</span>`)
    }
  } else {
    lines.push('')
    lines.push('<strong>运输详情:</strong>')
    transfers.forEach((t, i) => {
      const from = t.from || '?'
      const to = t.to || '?'
      const agv = t.agv || '?'
      const progress = t.progress != null ? `${t.progress}%` : '--'
      lines.push(`  ${i + 1}. ${agv}: ${from} → ${to} (${progress})`)
    })

    // 按 AGV 统计
    const agvUsage = {}
    transfers.forEach(t => {
      const agv = t.agv || 'unknown'
      agvUsage[agv] = (agvUsage[agv] || 0) + 1
    })
    lines.push('')
    lines.push('<strong>AGV 任务分配:</strong>')
    Object.entries(agvUsage).forEach(([agv, count]) => {
      lines.push(`  ${agv}: ${count} 个任务`)
    })
  }

  return lines.join('\n')
}

function buildEventSummary() {
  const events = monitorStore.events
  const total = monitorStore.totalEventCount

  if (events.length === 0) {
    return [`<strong>日志事件摘要</strong>`, ``, `暂无系统日志`].join('\n')
  }

  // 按类型统计
  const typeCount = {}
  events.forEach(e => {
    typeCount[e.type] = (typeCount[e.type] || 0) + 1
  })

  const typeLabels = {
    success: '✅ 成功', warning: '⚠️ 警告', error: '❌ 错误',
    info: 'ℹ️ 信息', task: '📌 任务', agv: '🚛 AGV', machine: '⚙️ 机器',
  }

  const lines = [
    `<strong>日志事件摘要</strong>`,
    ``,
    `<strong>总事件:</strong> ${total} (当前缓冲: ${events.length})`,
    ``,
    `<strong>分类统计:</strong>`,
  ]

  Object.entries(typeCount)
    .sort((a, b) => b[1] - a[1])
    .forEach(([type, count]) => {
      lines.push(`  ${typeLabels[type] || type}: ${count}`)
    })

  // 最近异常
  const warnings = events.filter(e => e.type === 'warning' || e.type === 'error')
  if (warnings.length > 0) {
    lines.push('')
    lines.push(`<span class="tag-warn">⚠ 近期异常 (${warnings.length}):</span>`)
    warnings.slice(-3).forEach(w => {
      lines.push(`  [${w.type}] ${w.title}`)
    })
  }

  return lines.join('\n')
}

function buildCustomAnalysis(query) {
  const q = query.toLowerCase()
  const lines = [
    `<strong>自定义分析</strong>`,
    ``,
    `<strong>问题:</strong> ${query}`,
    ``,
  ]

  // 简单关键词匹配，给出相关数据
  if (q.includes('agv') || q.includes('车辆') || q.includes('agent')) {
    lines.push(`<strong>AGV 数据:</strong>`)
    lines.push(`  总数: ${agvList.value.length}, 活跃: ${activeCount.value}`)
    lines.push(`  活跃率: ${agvList.value.length > 0 ? ((activeCount.value / agvList.value.length) * 100).toFixed(1) : 0}%`)
  }

  if (q.includes('机器') || q.includes('machine') || q.includes('负载')) {
    const machines = store.currentState?.machines || {}
    const ml = Object.values(machines)
    lines.push(`<strong>机器数据:</strong>`)
    lines.push(`  总数: ${ml.length}, 工作: ${ml.filter(m => m.status === 'WORKING').length}`)
  }

  if (q.includes('任务') || q.includes('task') || q.includes('运输') || q.includes('transfer')) {
    const transfers = store.currentState?.active_transfers || []
    lines.push(`<strong>运输数据:</strong>`)
    lines.push(`  活跃运输: ${transfers.length}`)
  }

  if (q.includes('日志') || q.includes('log') || q.includes('event') || q.includes('事件')) {
    lines.push(`<strong>日志数据:</strong>`)
    lines.push(`  总事件: ${monitorStore.totalEventCount}, 缓冲: ${monitorStore.events.length}`)
  }

  if (lines.length <= 4) {
    lines.push('暂未匹配到相关数据维度，请尝试包含关键词: AGV、机器、任务、日志')
  }

  lines.push('')
  lines.push(`<span class="tag-info">ℹ 后续将接入 AI 引擎，提供更智能的分析能力</span>`)

  return lines.join('\n')
}

function buildRecommendation(rate) {
  const r = Number(rate)
  if (r >= 80) return `<span class="tag-ok">✓ 效率良好，AGV 利用充分</span>`
  if (r >= 50) return `<span class="tag-info">💡 建议: 考虑优化任务分配策略，减少 AGV 空闲</span>`
  return `<span class="tag-warn">⚠ 效率偏低，大量 AGV 空闲，建议检查调度算法或任务量配置</span>`
}

// --- 简单渲染: 换行 → <br> ---
const renderedResult = computed(() => {
  if (!analysisResult.value) return ''
  return analysisResult.value
    .replace(/\n/g, '<br>')
    .replace(/  /g, '&nbsp;&nbsp;')
})
</script>

<style scoped>
.agent-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  color: rgba(200, 220, 255, 0.85);
  font-size: 12px;
}

/* ==================== Agent 状态区 ==================== */

.agent-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 4px;
}

.agent-header h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: rgba(200, 220, 255, 0.9);
}

.agent-counter {
  font-size: 11px;
  color: rgba(100, 180, 255, 0.7);
  background: rgba(100, 180, 255, 0.1);
  padding: 2px 8px;
  border-radius: 10px;
}

.agent-empty {
  padding: 12px;
  text-align: center;
  color: rgba(160, 190, 230, 0.4);
}

.agent-list {
  max-height: 180px;
  overflow-y: auto;
  padding: 4px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.agent-list::-webkit-scrollbar {
  width: 3px;
}

.agent-list::-webkit-scrollbar-track {
  background: transparent;
}

.agent-list::-webkit-scrollbar-thumb {
  background: rgba(100, 180, 255, 0.15);
  border-radius: 2px;
}

.agent-card {
  background: rgba(100, 180, 255, 0.06);
  border: 1px solid rgba(100, 180, 255, 0.12);
  border-radius: 8px;
  padding: 6px 10px;
  cursor: pointer;
  transition: border-color 0.2s;
}

.agent-card:hover {
  border-color: rgba(100, 180, 255, 0.25);
}

.agent-card.active {
  border-left: 3px solid #4CAF50;
}

.agent-card.idle {
  border-left: 3px solid rgba(100, 180, 255, 0.2);
}

.agent-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.agent-icon {
  font-size: 13px;
}

.agent-name {
  flex: 1;
  font-size: 11px;
  font-weight: 600;
  color: rgba(200, 220, 255, 0.9);
}

.agent-status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.dot-active {
  background: #4CAF50;
  box-shadow: 0 0 4px rgba(76, 175, 80, 0.5);
}

.dot-idle {
  background: rgba(160, 190, 230, 0.3);
}

.agent-card-body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px 10px;
}

.agent-field {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.agent-field-label {
  font-size: 10px;
  color: rgba(160, 190, 230, 0.45);
}

.agent-field-value {
  font-size: 10px;
  color: rgba(200, 220, 255, 0.75);
  font-variant-numeric: tabular-nums;
}

.status-tag {
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 600;
}

.status-active {
  background: rgba(76, 175, 80, 0.15);
  color: #4CAF50;
}

.status-idle {
  background: rgba(160, 190, 230, 0.1);
  color: rgba(160, 190, 230, 0.45);
}

.agent-summary {
  display: flex;
  gap: 6px;
  padding: 6px 12px;
  border-top: 1px solid rgba(100, 180, 255, 0.08);
  border-bottom: 1px solid rgba(100, 180, 255, 0.08);
  background: rgba(100, 180, 255, 0.03);
  flex-shrink: 0;
}

.summary-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
}

.summary-label {
  font-size: 9px;
  color: rgba(160, 190, 230, 0.45);
}

.summary-value {
  font-size: 13px;
  font-weight: 700;
  color: rgba(200, 220, 255, 0.9);
  font-variant-numeric: tabular-nums;
}

.active-text { color: #4CAF50; }
.idle-text { color: rgba(160, 190, 230, 0.45); }

/* ==================== 分析区域 ==================== */

.analysis-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 8px 12px 10px;
  gap: 6px;
}

.analysis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.analysis-title {
  font-size: 12px;
  font-weight: 600;
  color: rgba(200, 220, 255, 0.9);
}

.clear-analysis-btn {
  background: none;
  border: none;
  color: rgba(160, 190, 230, 0.4);
  font-size: 14px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
}

.clear-analysis-btn:hover {
  color: rgba(200, 220, 255, 0.8);
}

/* 模板按钮 */
.template-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.template-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid rgba(100, 180, 255, 0.12);
  border-radius: 6px;
  background: rgba(100, 180, 255, 0.04);
  color: rgba(200, 220, 255, 0.7);
  font-size: 10px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.template-btn:hover:not(:disabled) {
  background: rgba(100, 180, 255, 0.1);
  border-color: rgba(100, 180, 255, 0.25);
  color: rgba(200, 220, 255, 0.95);
}

.template-btn.active {
  background: rgba(100, 180, 255, 0.15);
  border-color: rgba(100, 180, 255, 0.35);
  color: rgba(200, 220, 255, 0.95);
}

.template-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.tpl-icon {
  font-size: 11px;
}

.tpl-label {
  font-size: 10px;
  font-weight: 500;
}

/* 输入行 */
.analysis-input-row {
  display: flex;
  gap: 4px;
}

.analysis-input {
  flex: 1;
  padding: 5px 8px;
  border: 1px solid rgba(100, 180, 255, 0.12);
  border-radius: 6px;
  background: rgba(20, 25, 45, 0.5);
  color: rgba(200, 220, 255, 0.9);
  font-size: 11px;
  outline: none;
  transition: border-color 0.2s;
}

.analysis-input::placeholder {
  color: rgba(160, 190, 230, 0.3);
}

.analysis-input:focus {
  border-color: rgba(100, 180, 255, 0.3);
}

.send-btn {
  padding: 4px 10px;
  border: 1px solid rgba(100, 180, 255, 0.2);
  border-radius: 6px;
  background: rgba(100, 180, 255, 0.1);
  color: rgba(200, 220, 255, 0.8);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}

.send-btn:hover:not(:disabled) {
  background: rgba(100, 180, 255, 0.2);
  color: rgba(200, 220, 255, 1);
}

.send-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* 分析结果展示框 */
.analysis-result-box {
  flex: 1;
  min-height: 80px;
  max-height: 200px;
  overflow-y: auto;
  padding: 8px 10px;
  border: 1px solid rgba(100, 180, 255, 0.08);
  border-radius: 8px;
  background: rgba(10, 14, 28, 0.5);
  line-height: 1.5;
}

.analysis-result-box.has-content {
  border-color: rgba(100, 180, 255, 0.15);
}

.analysis-result-box::-webkit-scrollbar {
  width: 3px;
}

.analysis-result-box::-webkit-scrollbar-track {
  background: transparent;
}

.analysis-result-box::-webkit-scrollbar-thumb {
  background: rgba(100, 180, 255, 0.15);
  border-radius: 2px;
}

.result-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 60px;
  color: rgba(160, 190, 230, 0.25);
  font-size: 11px;
  text-align: center;
}

.result-content {
  font-size: 11px;
  color: rgba(200, 220, 255, 0.8);
  word-break: break-word;
}

.result-content :deep(strong) {
  color: rgba(200, 220, 255, 0.95);
  font-weight: 600;
}

.result-content :deep(.tag-ok) {
  color: rgba(76, 175, 80, 0.9);
}

.result-content :deep(.tag-warn) {
  color: rgba(255, 152, 0, 0.9);
}

.result-content :deep(.tag-info) {
  color: rgba(100, 180, 255, 0.8);
}
</style>
