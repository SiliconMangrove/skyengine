/**
 * Simulation Config Composable
 * 统一管理三个工厂视图共享的算法配置与执行逻辑
 */
import { ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { apiPost, API_ROUTES } from '@/utils/api';
import { runFullSystemTest } from '@/scenarios/fullSystemTest';
import { backendSystemTest } from '@/scenarios/backendSystemTest';
import { useAnalysisLogStore } from '@/stores/analysisLog';

export function useSimulationConfig({
  defaults = {},
  defaultOptions = {},
  fetchFromApi = false,
} = {}) {
  // ==================== 算法选项 ====================

  const fjspOptions = ref(defaultOptions.fjsp || []);
  const mapfOptions = ref(defaultOptions.mapf || []);
  const assignerOptions = ref(defaultOptions.assigner || []);

  const selectedFjsp = ref(defaults.fjsp || '');
  const selectedMapf = ref(defaults.mapf || '');
  const selectedAssigner = ref(defaults.assigner || '');

  const algorithmString = computed(
    () => `${selectedFjsp.value}+${selectedMapf.value}+${selectedAssigner.value}`,
  );

  // ==================== 状态 ====================

  const isRunningTest = ref(false);
  let stopTest = null;

  // ==================== 模块③：episode 结束快照到 analysisLog（async，落盘到后端）====================

  async function snapshotToAnalysisLog(store, monitorStore, meta) {
    try {
      const analysisLog = useAnalysisLogStore()
      const run = await analysisLog.finalizeFromStores(store, monitorStore, meta)
      ElMessage.success(
        `已保存分析样本：${run.total_steps} 步（系统日志 tab → 📊 查看历史样本）`,
      )
      return run
    } catch (err) {
      console.warn('[useSimulationConfig] snapshotToAnalysisLog 失败:', err)
      ElMessage.error(`分析样本保存失败：${err.message || err}`)
      return null
    }
  }

  // ==================== 从 API 获取算法选项 ====================

  async function fetchAlgoOptions() {
    try {
      const data = await apiPost(API_ROUTES.ALGO, {});
      if (data && typeof data === 'object' && !Array.isArray(data)) {
        if (data.fjsp?.options) fjspOptions.value = data.fjsp.options;
        if (data.mapf?.options) mapfOptions.value = data.mapf.options;
        if (data.assigner?.options) assignerOptions.value = data.assigner.options;
      }
    } catch (error) {
      console.warn('[useSimulationConfig] 获取算法列表失败，使用默认值:', error);
    }

    // 选中第一个可用选项
    const firstEnabled = (arr) => arr.find((o) => !o.disabled);
    const fjsp = firstEnabled(fjspOptions.value);
    const mapf = firstEnabled(mapfOptions.value);
    const assigner = firstEnabled(assignerOptions.value);
    if (fjsp) selectedFjsp.value = fjsp.value;
    if (mapf) selectedMapf.value = mapf.value;
    if (assigner) selectedAssigner.value = assigner.value;
  }

  // ==================== 前端模式执行 ====================

  function executeFrontend(store, monitorStore, onFinish) {
    const algorithm = algorithmString.value;
    ElMessage.success(`启动模拟: ${algorithm}`);

    stopTest = runFullSystemTest(store, monitorStore, () => {
      isRunningTest.value = false;
      stopTest = null;
      ElMessage.success('模拟完成');
      snapshotToAnalysisLog(store, monitorStore, { factory_id: store.selectedFactoryId, algorithm });
      onFinish?.();
    });
  }

  // ==================== 后端模式执行 ====================

  async function executeBackend(store, monitorStore, onFinish) {
    const algorithm = algorithmString.value;

    try {
      stopTest = await backendSystemTest(
        store,
        monitorStore,
        { algorithm },
        () => {
          isRunningTest.value = false;
          stopTest = null;
          ElMessage.success('仿真执行完成');
          snapshotToAnalysisLog(store, monitorStore, { factory_id: store.selectedFactoryId, algorithm });
          onFinish?.();
        },
      );
    } catch (error) {
      isRunningTest.value = false;
      stopTest = null;
      ElMessage.error(`仿真执行失败: ${error.message}`);
    }
  }

  // ==================== Docker 模式执行 ====================

  async function executeDocker(store, monitorStore, onFinish) {
    const algorithm = algorithmString.value;

    try {
      await apiPost(API_ROUTES.FACTORY_ALGORITHM_SET, { algorithm }, { timeout: 30000 });
      await apiPost(API_ROUTES.FACTORY_CONTROL_RESET, null, { timeout: 30000 });

      stopTest = await backendSystemTest(store, monitorStore, { algorithm }, () => {
        isRunningTest.value = false;
        stopTest = null;
        ElMessage.success('容器化仿真执行完成');
        snapshotToAnalysisLog(store, monitorStore, { factory_id: store.selectedFactoryId, algorithm });
        onFinish?.();
      });
    } catch (error) {
      isRunningTest.value = false;
      stopTest = null;
      ElMessage.error(`仿真执行失败: ${error.message}`);
    }
  }

  // ==================== 统一执行入口 ====================

  function handleExecutePlan(store, monitorStore, { mode, onFinish } = {}) {
    if (isRunningTest.value) return;
    isRunningTest.value = true;

    switch (mode) {
      case 'frontend':
        executeFrontend(store, monitorStore, onFinish);
        break;
      case 'backend':
        executeBackend(store, monitorStore, onFinish);
        break;
      case 'docker':
        executeDocker(store, monitorStore, onFinish);
        break;
      default:
        isRunningTest.value = false;
        console.warn(`[useSimulationConfig] 未知执行模式: ${mode}`);
    }
  }

  // ==================== 停止 ====================

  function handleStop(store) {
    if (stopTest) {
      stopTest();
      stopTest = null;
    }
    isRunningTest.value = false;
    if (store) store.isPlaying = false;
    ElMessage.info('已停止模拟');
  }

  // ==================== 清理 ====================

  function cleanup(store) {
    if (stopTest) {
      stopTest();
      stopTest = null;
    }
    if (store) store.clearAll();
  }

  return {
    // 算法选项
    fjspOptions,
    mapfOptions,
    assignerOptions,
    selectedFjsp,
    selectedMapf,
    selectedAssigner,
    algorithmString,

    // 状态
    isRunningTest,

    // 方法
    handleExecutePlan,
    handleStop,
    fetchAlgoOptions,
    cleanup,
  };
}
