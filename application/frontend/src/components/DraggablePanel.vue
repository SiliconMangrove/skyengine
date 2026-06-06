<template>
  <div
    class="draggable-panel"
    :class="{ collapsed: isCollapsed, dragging: isDragging }"
    :style="panelStyle"
    @mouseenter="hovered = true"
    @mouseleave="hovered = false"
  >
    <!-- 标题栏 (拖拽手柄) -->
    <div class="dp-header" @mousedown.prevent="startDrag" @touchstart.prevent="startDragTouch">
      <div class="dp-header-left">
        <span v-if="icon" class="dp-icon">{{ icon }}</span>
        <span class="dp-title">{{ title }}</span>
      </div>
      <div class="dp-header-actions">
        <button class="dp-action-btn" @click.stop="isCollapsed = !isCollapsed" :title="isCollapsed ? '展开' : '折叠'">
          <span class="dp-chevron" :class="{ rotated: isCollapsed }">&#9662;</span>
        </button>
        <button class="dp-action-btn dp-close-btn" @click.stop="$emit('close')" title="关闭">
          &times;
        </button>
      </div>
    </div>

    <!-- 内容区 -->
    <div class="dp-body" v-show="!isCollapsed">
      <slot></slot>
    </div>

    <!-- 折叠态摘要 (可选) -->
    <div class="dp-collapsed-hint" v-if="isCollapsed && $slots.hint">
      <slot name="hint"></slot>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  title: { type: String, default: '面板' },
  icon: { type: String, default: '' },
  width: { type: Number, default: 300 },
  /** 初始位置 { x, y }，不传则自动定位 */
  initialPos: { type: Object, default: null },
  /** 是否默认折叠 */
  defaultCollapsed: { type: Boolean, default: false },
  /** 最小宽度 */
  minWidth: { type: Number, default: 200 },
  /** 固定高度 (px)，0 = 自适应 */
  height: { type: Number, default: 0 },
  /** 最大高度 (px)，超出内容滚动 */
  maxHeight: { type: Number, default: 0 },
})

const emit = defineEmits(['close', 'collapse', 'move'])

// ==================== 拖拽状态 ====================

const posX = ref(0)
const posY = ref(0)
const isDragging = ref(false)
const isCollapsed = ref(props.defaultCollapsed)
const hovered = ref(false)

let dragOffsetX = 0
let dragOffsetY = 0

const panelStyle = computed(() => {
  const style = {
    width: `${props.width}px`,
    minWidth: `${props.minWidth}px`,
    transform: `translate(${posX.value}px, ${posY.value}px)`,
  }
  if (props.height > 0) {
    style.height = `${props.height}px`
  }
  if (props.maxHeight > 0) {
    style.maxHeight = `${props.maxHeight}px`
  }
  return style
})

// ==================== 拖拽逻辑 ====================

function startDrag(e) {
  isDragging.value = true
  dragOffsetX = e.clientX - posX.value
  dragOffsetY = e.clientY - posY.value
  document.addEventListener('mousemove', onDrag)
  document.addEventListener('mouseup', stopDrag)
}

function startDragTouch(e) {
  const touch = e.touches[0]
  isDragging.value = true
  dragOffsetX = touch.clientX - posX.value
  dragOffsetY = touch.clientY - posY.value
  document.addEventListener('touchmove', onDragTouch, { passive: false })
  document.addEventListener('touchend', stopDragTouch)
}

function onDrag(e) {
  let nx = e.clientX - dragOffsetX
  let ny = e.clientY - dragOffsetY
  // 边界约束
  nx = Math.max(-props.width + 60, Math.min(window.innerWidth - 60, nx))
  ny = Math.max(0, Math.min(window.innerHeight - 40, ny))
  posX.value = nx
  posY.value = ny
}

function onDragTouch(e) {
  e.preventDefault()
  const touch = e.touches[0]
  let nx = touch.clientX - dragOffsetX
  let ny = touch.clientY - dragOffsetY
  nx = Math.max(-props.width + 60, Math.min(window.innerWidth - 60, nx))
  ny = Math.max(0, Math.min(window.innerHeight - 40, ny))
  posX.value = nx
  posY.value = ny
}

function stopDrag() {
  isDragging.value = false
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', stopDrag)
  emit('move', { x: posX.value, y: posY.value })
}

function stopDragTouch() {
  isDragging.value = false
  document.removeEventListener('touchmove', onDragTouch)
  document.removeEventListener('touchend', stopDragTouch)
  emit('move', { x: posX.value, y: posY.value })
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', stopDrag)
  document.removeEventListener('touchmove', onDragTouch)
  document.removeEventListener('touchend', stopDragTouch)
})

// ==================== 初始化位置 ====================

onMounted(() => {
  if (props.initialPos) {
    posX.value = props.initialPos.x
    posY.value = props.initialPos.y
  }
})
</script>

<style scoped>
@import './styles/DraggablePanel.css';
</style>
