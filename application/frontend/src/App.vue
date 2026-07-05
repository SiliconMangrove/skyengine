<script setup>
import { RouterView } from 'vue-router'
import GlobalTopNav from '@/components/GlobalTopNav.vue'
import AlgoPoolPanel from '@/components/AlgoPoolPanel.vue'
import BatchPanel from '@/components/BatchPanel.vue'
import { useUiPanelStore } from '@/stores/uiPanel'

const ui = useUiPanelStore()
</script>

<template>
  <RouterView />
  <GlobalTopNav />
  <AlgoPoolPanel v-if="ui.algoPoolOpen" />
  <BatchPanel v-if="ui.batchOpen" />
</template>

<style scoped>
:global(body) {
  margin: 0;
  padding: 0;
}


@media (min-width: 1024px) {
  header {
    display: flex;
    place-items: center;
    padding-right: calc(var(--section-gap) / 2);
  }

  .logo {
    margin: 0 2rem 0 0;
  }

  header .wrapper {
    display: flex;
    place-items: flex-start;
    flex-wrap: wrap;
  }

  nav {
    text-align: left;
    margin-left: -1rem;
    font-size: 1rem;

    padding: 1rem 0;
    margin-top: 1rem;
  }
}
</style>
