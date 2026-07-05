/**
 * 全局浮层面板开关状态
 *
 * 算法池 / 批处理实验 改造为浮层面板（不再走路由），
 * 由 GlobalTopNav 和各页面入口调用 toggle / open / close。
 *
 * 设计与 analysisLog.store.panelOpen 同模式：
 *   - panelOpen: ref<boolean>
 *   - openPanel() / closePanel() / togglePanel()
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiPanelStore = defineStore('uiPanel', () => {
  const algoPoolOpen = ref(false)
  const batchOpen = ref(false)

  // 全局导航状态：'home'（首页）/ 'selector'（工作空间选择页）/ 'factory'（具体工厂操作页）/ 'loading'（加载过渡态，隐藏全部）
  // 由 HomeView / FactoryView 在切换时写入，GlobalTopNav 据此决定显示哪个按钮
  const navState = ref('home')
  function setNavState(s) {
    if (s === 'home' || s === 'selector' || s === 'factory' || s === 'loading') navState.value = s
  }

  // 工厂加载过渡态（"工厂正在准备中" / "工厂资源正在清理中"）。
  // 独立于 navState：只要为 true，GlobalTopNav 完全不渲染，避免退出/进入时
  // navState 在 stopLoading → HomeView mount 之间短暂落到 'selector' 闪出按钮的 race。
  const factoryLoading = ref(false)
  function setFactoryLoading(v) { factoryLoading.value = !!v }

  function openAlgoPool() { algoPoolOpen.value = true }
  function closeAlgoPool() { algoPoolOpen.value = false }
  function toggleAlgoPool() { algoPoolOpen.value = !algoPoolOpen.value }

  function openBatch() { batchOpen.value = true }
  function closeBatch() { batchOpen.value = false }
  function toggleBatch() { batchOpen.value = !batchOpen.value }

  // 互斥：同时只允许一个浮层在最前（避免遮挡）
  // 打开一个时关掉另一个
  function showAlgoPool() { batchOpen.value = false; algoPoolOpen.value = true }
  function showBatch() { algoPoolOpen.value = false; batchOpen.value = true }

  // 关闭所有浮层面板（退出工厂 / 切页时调用，避免浮层残留）
  function closeAllFloatingPanels() {
    algoPoolOpen.value = false
    batchOpen.value = false
  }

  return {
    algoPoolOpen,
    batchOpen,
    navState,
    setNavState,
    factoryLoading,
    setFactoryLoading,
    openAlgoPool, closeAlgoPool, toggleAlgoPool,
    openBatch, closeBatch, toggleBatch,
    showAlgoPool, showBatch,
    closeAllFloatingPanels,
  }
})
