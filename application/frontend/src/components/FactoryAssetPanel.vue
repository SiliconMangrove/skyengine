<template>
  <div class="factory-asset-panel">
    <div class="panel-header">
      <h3>🏭 工厂资产</h3>
      <div class="header-actions">
        <button
          class="edit-toggle-btn"
          :class="{ active: isEditMode }"
          @click="toggleEditMode"
        >
          {{ isEditMode ? '退出编辑' : '进入编辑' }}
        </button>
        <button
          v-if="isEditMode"
          class="export-btn"
          @click="exportConfig"
        >
          导出配置
        </button>
      </div>
    </div>

    <div class="panel-body">
      <!-- 资产池（编辑模式可用） -->
      <div v-if="isEditMode" class="asset-pool">
        <div class="asset-pool-header">资产池</div>
        <div class="asset-pool-body">
          <el-select
            v-model="selectedTemplateId"
            placeholder="选择数字资产"
            size="small"
            class="asset-select"
          >
            <el-option-group label="区域类">
              <el-option
                v-for="t in zoneTemplates"
                :key="t.id"
                :label="t.icon + ' ' + t.name"
                :value="t.id"
              />
            </el-option-group>
            <el-option-group label="设备类">
              <el-option
                v-for="t in machineTemplates"
                :key="t.id"
                :label="t.icon + ' ' + t.name"
                :value="t.id"
              />
            </el-option-group>
            <el-option-group label="运输类">
              <el-option
                v-for="t in agvTemplates"
                :key="t.id"
                :label="t.icon + ' ' + t.name"
                :value="t.id"
              />
            </el-option-group>
            <el-option-group label="路由点类">
              <el-option
                v-for="t in waypointTemplates"
                :key="t.id"
                :label="t.icon + ' ' + t.name"
                :value="t.id"
              />
            </el-option-group>
            <el-option-group label="装饰类">
              <el-option
                v-for="t in decorTemplates"
                :key="t.id"
                :label="t.icon + ' ' + t.name"
                :value="t.id"
              />
            </el-option-group>
          </el-select>

          <!-- 预览槽 -->
          <div class="preview-slot" v-if="selectedTemplate">
            <div class="preview-shape" :class="'preview-' + selectedTemplate.type">
              <span class="preview-icon">{{ selectedTemplate.icon }}</span>
            </div>
            <span class="preview-name">{{ selectedTemplate.name }}</span>
          </div>

          <el-button
            size="small"
            type="primary"
            :disabled="!selectedTemplateId"
            @click="addAsset"
            class="add-asset-btn"
          >
            + 添加到场景
          </el-button>
        </div>
      </div>

      <!-- 资产统计 -->
      <div v-if="store.currentConfigId" class="assets-stats">
        <div class="stat-item">
          <span class="stat-label">区域:</span>
          <span class="stat-value">{{ assetsStats.zoneCount }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">机器:</span>
          <span class="stat-value">{{ assetsStats.machineCount }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">路由点:</span>
          <span class="stat-value">{{ assetsStats.waypointCount }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">AGV:</span>
          <span class="stat-value">{{ assetsStats.agvCount }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">总计:</span>
          <span class="stat-value">{{ assetsStats.totalAssets }}</span>
        </div>
      </div>

      <!-- 资产列表 -->
      <div class="services-list-container">
        <div v-if="assetsList.length === 0" class="empty-state">
          <div class="empty-icon">📦</div>
          <p>{{ isEditMode ? '从资产池添加数字资产' : '请先选择或上传配置文件' }}</p>
        </div>
        <ul v-else class="services-list">
          <li
            v-for="(asset, index) in assetsList"
            :key="index"
            class="service-item"
            :class="[
              `asset-type-${asset.type}`,
              { 'edit-mode': isEditMode }
            ]"
          >
            <!-- 编辑模式：可重命名 + 删除 -->
            <div v-if="isEditMode" class="editable-node">
              <span class="node-icon">{{ asset.icon }}</span>
              <input
                class="rename-input"
                :value="asset.name"
                @change="renameAsset(asset, $event.target.value)"
                @keyup.enter="$event.target.blur()"
                :title="'点击编辑名称'"
              />
              <button class="delete-btn" @click="removeAsset(asset)" title="删除">✕</button>
            </div>

            <!-- 非编辑模式：拖拽节点 -->
            <el-tooltip v-else placement="left" :open-delay="500" effect="dark">
              <template #content>
                <div class="tooltip-content">{{ asset.description }}</div>
              </template>
              <div
                class="draggable-node"
                draggable="true"
                @dragstart="onDragStart($event, asset)"
              >
                <span class="node-icon">{{ asset.icon }}</span>
                <span class="node-name">{{ asset.name }}</span>
                <span class="drag-handle">⋮⋮</span>
              </div>
            </el-tooltip>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useFactoryStore, ASSET_TEMPLATES } from '@/stores/factory'

const store = useFactoryStore()

// --- Edit Mode ---
const isEditMode = ref(false)
const emit = defineEmits(['edit-mode-change'])

function toggleEditMode() {
  isEditMode.value = !isEditMode.value
  emit('edit-mode-change', isEditMode.value)
}

// --- Asset Pool ---
const selectedTemplateId = ref(null)

const selectedTemplate = computed(() => {
  if (!selectedTemplateId.value) return null
  return ASSET_TEMPLATES[selectedTemplateId.value] || null
})

const zoneTemplates = computed(() =>
  Object.entries(ASSET_TEMPLATES)
    .filter(([, t]) => t.type === 'zone')
    .map(([id, t]) => ({ id, ...t }))
)

const machineTemplates = computed(() =>
  Object.entries(ASSET_TEMPLATES)
    .filter(([, t]) => t.type === 'machine')
    .map(([id, t]) => ({ id, ...t }))
)

const agvTemplates = computed(() =>
  Object.entries(ASSET_TEMPLATES)
    .filter(([, t]) => t.type === 'agv')
    .map(([id, t]) => ({ id, ...t }))
)

const waypointTemplates = computed(() =>
  Object.entries(ASSET_TEMPLATES)
    .filter(([, t]) => t.type === 'waypoint')
    .map(([id, t]) => ({ id, ...t }))
)

const decorTemplates = computed(() =>
  Object.entries(ASSET_TEMPLATES)
    .filter(([, t]) => t.template?.decor)
    .map(([id, t]) => ({ id, ...t }))
)

function addAsset() {
  if (!selectedTemplateId.value) return
  try {
    const newId = store.addAssetFromTemplate(selectedTemplateId.value)
    ElMessage.success(`已添加资产: ${newId}`)
  } catch (e) {
    ElMessage.error(e.message)
  }
}

// --- Asset List ---
const assetsList = computed(() => {
  if (!store.currentConfigId) return []
  return store.formatAssetsList()
})

const assetsStats = computed(() => store.getAssetsStats())

function renameAsset(asset, newName) {
  if (!newName || !newName.trim()) return
  store.renameAsset(asset.type, asset.data.id, newName.trim())
}

function removeAsset(asset) {
  store.removeAsset(asset.type, asset.data.id)
  ElMessage.success(`已移除: ${asset.name}`)
}

// --- Export ---
function exportConfig() {
  const config = store.exportCurrentConfig()
  if (!config) {
    ElMessage.warning('没有可导出的配置')
    return
  }
  const dataStr = JSON.stringify(config, null, 2)
  const blob = new Blob([dataStr], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${config.name || config.id || 'factory_config'}.json`
  link.click()
  URL.revokeObjectURL(url)
  ElMessage.success('配置已导出')
}

// --- Drag (non-edit mode) ---
const onDragStart = (event, service) => {
  event.dataTransfer.setData('application/node-data', JSON.stringify(service))
  event.dataTransfer.effectAllowed = 'copy'
}
</script>

<style scoped>
@import './styles/FactoryAssetPanel.css';
</style>
