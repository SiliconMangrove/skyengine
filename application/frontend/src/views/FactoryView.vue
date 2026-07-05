<template>
  <div class="factory-container">
    <div class="bg-overlay"></div>

    <transition name="fade" mode="out-in">
      <!-- 加载过渡页 -->
      <div class="factory-loading" v-if="isLoading" key="loading">
        <div class="loading-content">
          <div class="loading-spinner-ring">
            <svg viewBox="0 0 100 100">
              <circle class="ring-bg" cx="50" cy="50" r="42" />
              <circle class="ring-fg" cx="50" cy="50" r="42" />
            </svg>
          </div>
          <h2 class="loading-title">{{ loadingTitle }}</h2>
          <p class="loading-subtitle">{{ loadingSubtitle }}</p>
          <div class="loading-timer" v-if="loadingElapsed > 0">{{ loadingElapsed }}s</div>
        </div>
      </div>

      <!-- 工厂选择器 -->
      <div class="factory-selector" v-else-if="!isInFactory" key="selector">
        <div class="nav-bar">
          <button class="btn-home" @click="backToHome">
            <span class="icon">←</span>
            <span class="text">返回主页</span>
          </button>
          <button class="btn-algo-pool" @click="goToAlgorithms">
            算法池
          </button>
        </div>

        <div class="selector-content">
          <div class="selector-header">
            <h1>工作空间选择</h1>
            <p>Select Your Factory Workspace</p>
            <div class="deco-line"></div>
          </div>

          <div class="scroll-box-container">
            <div class="factory-grid">
              <div v-for="factory in factories" :key="factory.id" class="factory-card"
                @click="enterFactory(factory.id)">
                <div class="card-image">
                  <img :src="factory.image ||
                    'https://images.unsplash.com/photo-1581091226825-a6a2a5aee158?auto=format&fit=crop&w=800'
                    " :alt="factory.name" />
                  <div class="hover-overlay">
                    <span class="enter-btn">进入车间 →</span>
                  </div>
                </div>
                <div class="card-content">
                  <div class="content-top">
                    <h3>{{ factory.name }}</h3>
                    <div class="status-dot"></div>
                  </div>
                  <span class="id-tag">ID: {{ factory.id }}</span>
                  <p class="desc">{{ factory.description }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 工厂内容 -->
      <div class="factory-content" v-else key="content">
        <div class="content-header">
          <div class="header-left">
            <button class="btn-back-internal" @click="backToSelector">
              <span class="icon">✕</span> 退出工作台
            </button>
            <div class="divider"></div>
            <div class="header-info">
              <h2>{{ currentFactory.name }}</h2>
              <span class="header-badge">LIVE</span>
            </div>
          </div>
        </div>

        <div class="component-wrapper">
          <component :is="currentFactoryComponent" :factory="currentFactory" />
        </div>

        <!-- 离线分析浮层：作为工厂内组件存在，不走路由，避免卸载 FactoryView 打断执行流 -->
        <AnalysisOverlayPanel
          v-if="analysisLog.panelOpen"
          @close="analysisLog.closePanel()"
        />
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed, defineAsyncComponent, onMounted, onBeforeUnmount, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRouter, useRoute, onBeforeRouteLeave } from "vue-router";
import FactoryPlayerSSE from "@/components/FactoryPlayerSSE.vue";
import AnalysisOverlayPanel from "@/components/AnalysisOverlayPanel.vue";
import { useFactoryStore } from "@/stores/factory";
import { useAnalysisLogStore } from "@/stores/analysisLog";
import { useUiPanelStore } from "@/stores/uiPanel";
import { apiPost, API_ROUTES } from "@/utils/api";

const factoryStore = useFactoryStore();
const analysisLog = useAnalysisLogStore();
const ui = useUiPanelStore();
const router = useRouter();
const route = useRoute();

// 用于跨页面回到原工厂：进入工厂时记到 sessionStorage，离开后回来时读出来
const LAST_FACTORY_KEY = 'skyengine_last_factory'
function rememberFactory(id) {
  if (id) {
    try {
      sessionStorage.setItem(LAST_FACTORY_KEY, id)
    } catch (e) {}
  }
}
function recallFactory() {
  try { return sessionStorage.getItem(LAST_FACTORY_KEY) || null } catch (e) { return null }
}

// 在 setup 阶段读 query.factory，把 isInFactory / currentFactoryId 初始值设对，
// 避免 onMounted 才切换导致"选择器闪一下再进工厂"。
// （离线分析已改为工厂内浮层组件，不再走路由，无需 session 快速恢复机制）
function _readInitialFactoryState() {
  const q = route.query?.factory
  if (q && typeof q === 'string') {
    return { factoryId: q }
  }
  return { factoryId: null }
}

const _initial = _readInitialFactoryState()
const _initialValid =
  _initial.factoryId &&
  factoryStore.getFactories().some((f) => f.id === _initial.factoryId)

const isInFactory = ref(false)
const currentFactoryId = ref(_initialValid ? _initial.factoryId : null)
// 标记是否需要走 enterFactory（query 深链）
const _needSlowEnter = !!_initialValid

// 同步导航状态到全局 store：进入工厂前默认是 selector，进入后切到 factory
// 加载过渡（isLoading=true）期间 ui.factoryLoading=true 会强制隐藏 GlobalTopNav，
// 所以这里 navState 即使短暂错位也不会闪按钮
ui.setNavState(_needSlowEnter ? 'factory' : 'selector')
watch(isInFactory, (v) => ui.setNavState(v ? 'factory' : 'selector'))

// 加载过渡状态
const isLoading = ref(false);
const loadingTitle = ref("");
const loadingSubtitle = ref("");
const loadingElapsed = ref(0);
let loadingTimer = null;

function startLoading(title, subtitle = "") {
  isLoading.value = true;
  ui.setFactoryLoading(true);
  ui.setNavState('loading');
  loadingTitle.value = title;
  loadingSubtitle.value = subtitle;
  loadingElapsed.value = 0;
  loadingTimer = setInterval(() => {
    loadingElapsed.value++;
  }, 1000);
}

function stopLoading() {
  isLoading.value = false;
  ui.setFactoryLoading(false);
  if (loadingTimer) {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }
}

// 工厂配置列表
const factories = computed(() => factoryStore.getFactories());

const currentFactory = computed(
  () => factories.value.find((f) => f.id === currentFactoryId.value) || {},
);

// 统一清理函数：断联后端 + 清理 store
async function cleanupFactory() {
  try {
    await apiPost(API_ROUTES.FACTORY_CONTROL_DISCONNECT);
  } catch (e) {
    console.warn("[FactoryView] disconnect 失败:", e);
  }
  factoryStore.clearAll();
  // 关闭浮层面板（算法池 / 批处理实验），避免退出后仍挂在 App.vue 全局层
  ui.closeAllFloatingPanels();
  isInFactory.value = false;
  currentFactoryId.value = null;
}

// 进入工厂
const enterFactory = async (factoryId) => {
  startLoading("工厂正在准备中", "正在初始化资源，请稍候...");
  try {
    const response = await apiPost(API_ROUTES.FACTORY_CONTROL_SWITCH, {
      factory_id: factoryId,
    }, { timeout: 120000 });

    if (response.status === "ok") {
      currentFactoryId.value = factoryId;
      factoryStore.setCurrentFactory(factoryId);
      rememberFactory(factoryId);
      isInFactory.value = true;

      // 预取数据集（已 memo 化，ConfigPanel 挂载时直接命中缓存）
      factoryStore.fetchDatasets().catch((e) => {
        console.error("[FactoryView] 加载数据集失败:", e);
      });

      const factory = factories.value.find((f) => f.id === factoryId);
      ElMessage.success({
        message: `已连接: ${factory.name}`,
        duration: 1500,
      });
    } else {
      ElMessage.error({
        message: `切换工厂失败: ${response.message || "未知错误"}`,
        duration: 3000,
      });
    }
  } catch (error) {
    ElMessage.error({
      message: `切换工厂失败: ${error.message || "网络错误"}`,
      duration: 3000,
    });
  } finally {
    stopLoading();
  }
};

// 返回选择列表 — 统一清理
const backToSelector = async () => {
  startLoading("工厂资源正在清理中", "正在释放资源，请稍候...");
  try {
    await cleanupFactory();
  } finally {
    stopLoading();
  }
};

// 跳转算法池页面
const goToAlgorithms = () => {
  ui.toggleAlgoPool();
};

// 返回主页 (Router) — 先清理再跳转
const backToHome = async () => {
  startLoading("正在返回主页", "清理资源中...");
  try {
    await cleanupFactory();
  } finally {
    // 在 stopLoading 解除 loading 屏蔽之前就把 navState 推到 'home'，
    // 避免 navState 短暂停在 'selector' 闪一下算法池按钮
    ui.setNavState('home');
    stopLoading();
    await router.push("/");
  }
};

// 浏览器后退 / 路由跳转时清理（离线分析已改为工厂内浮层，不再走路由，无需特殊处理）
onBeforeRouteLeave(async () => {
  ui.setFactoryLoading(true)  // 期间隐藏 GlobalTopNav
  await cleanupFactory()
  ui.setFactoryLoading(false)
});

// onMounted 只处理 query 深链进入；isInFactory / currentFactoryId 初值已在 setup 阶段算好
onMounted(async () => {
  if (_needSlowEnter) {
    await enterFactory(currentFactoryId.value)
  }
});

// 组件卸载兜底：彻底清理 store
onBeforeUnmount(() => {
  stopLoading();
  factoryStore.clearAll()
  isInFactory.value = false
  currentFactoryId.value = null
});

const currentFactoryComponent = computed(() => {
  if (!currentFactory.value) return null;
  const factoryId = currentFactory.value.id;

  switch (factoryId) {
    case "grid_factory":
      return defineAsyncComponent(
        () => import("@/views/factory/GridFactoryManage.vue"),
      );
    case "packet_factory":
      return defineAsyncComponent(
        () => import("@/views/factory/PacketFactoryManage.vue"),
      );
    case "northeast_center":
      return defineAsyncComponent(
        () => import("@/views/factory/StaticFactoryManage.vue"),
      );
    case "southwest_logistics":
      return defineAsyncComponent(
        () => import("@/views/factory/ComingSoonFactory.vue"),
      );
    case "grid_factory_new":
      return defineAsyncComponent(
        () => import("@/views/factory/DockerFactoryManage.vue"),
      );
    default:
      return defineAsyncComponent(
        () => import("@/views/factory/FactoryManage.vue"),
      );
  }
});
</script>

<style scoped>
@import "./styles/FactoryView.scss";

/* 加载过渡页 */
.factory-loading {
  position: relative;
  z-index: 1;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.loading-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
}

.loading-spinner-ring {
  width: 80px;
  height: 80px;
}

.loading-spinner-ring svg {
  width: 100%;
  height: 100%;
  animation: loading-spin 1.5s linear infinite;
}

.ring-bg {
  fill: none;
  stroke: rgba(100, 180, 255, 0.15);
  stroke-width: 6;
}

.ring-fg {
  fill: none;
  stroke: #64b5ff;
  stroke-width: 6;
  stroke-linecap: round;
  stroke-dasharray: 180;
  stroke-dashoffset: 80;
}

@keyframes loading-spin {
  to { transform: rotate(360deg); }
}

.loading-title {
  font-size: 22px;
  font-weight: 600;
  color: #e2e8f0;
  letter-spacing: 0.5px;
}

.loading-subtitle {
  font-size: 14px;
  color: #94a3b8;
}

.loading-timer {
  font-size: 13px;
  color: #64b5ff;
  font-variant-numeric: tabular-nums;
  min-width: 40px;
  text-align: center;
}

/* 算法池入口 */
.btn-algo-pool {
  padding: 8px 20px;
  background: rgba(100, 181, 255, 0.1);
  border: 1px solid rgba(100, 181, 255, 0.3);
  border-radius: 8px;
  color: #64b5ff;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-algo-pool:hover {
  background: rgba(100, 181, 255, 0.2);
  border-color: rgba(100, 181, 255, 0.5);
}
</style>
