import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import FactoryView from '../views/FactoryView.vue'
import AlgorithmPoolView from '../views/AlgorithmPoolView.vue'
import AnalysisListView from '../views/analysis/AnalysisListView.vue'
import AnalysisDetailView from '../views/analysis/AnalysisDetailView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
      meta: { title: '首页' }
    },
    {
      path: '/factory',
      name: 'factory',
      component: FactoryView,
      meta: { title: '工厂管理系统' }
    },
    {
      path: '/algorithms',
      name: 'algorithms',
      component: AlgorithmPoolView,
      meta: { title: '算法池' }
    },
    {
      path: '/analysis',
      name: 'analysis-list',
      component: AnalysisListView,
      meta: { title: '离线分析样本' }
    },
    {
      path: '/analysis/:runId',
      name: 'analysis-detail',
      component: AnalysisDetailView,
      meta: { title: '分析详情' }
    },
  ],
})

export default router
