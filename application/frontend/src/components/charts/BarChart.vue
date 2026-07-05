<template>
  <div class="bar-chart-wrap">
    <v-chart
      v-if="hasData"
      :option="chartOption"
      :autoresize="true"
      class="bar-chart"
    />
    <div v-else class="empty-chart">
      <span>暂无数据</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart as EBarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([
  CanvasRenderer,
  EBarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
])

const props = defineProps({
  // [{ name, data: [{x, y}], color? }]
  series: { type: Array, default: () => [] },
  xLabel: { type: String, default: 'step' },
  yLabel: { type: String, default: '' },
  yMax: { type: Number, default: null },
  height: { type: String, default: '240px' },
  showDataZoom: { type: Boolean, default: true },
  // 'grouped'（并列）/ 'stacked'（堆叠）
  mode: { type: String, default: 'grouped' },
})

const hasData = computed(() =>
  props.series.some((s) => Array.isArray(s.data) && s.data.length > 0),
)

const chartOption = computed(() => {
  // 收集所有 x 值，保持有序
  const xSet = new Set()
  props.series.forEach((s) => {
    ;(s.data || []).forEach((p) => xSet.add(p.x))
  })
  const xValues = Array.from(xSet).sort((a, b) => Number(a) - Number(b))

  const series = props.series.map((s) => {
    const dataMap = new Map()
    ;(s.data || []).forEach((p) => dataMap.set(p.x, p.y))
    return {
      name: s.name || '',
      type: 'bar',
      stack: props.mode === 'stacked' ? 'total' : undefined,
      itemStyle: s.color ? { color: s.color } : undefined,
      data: xValues.map((x) => dataMap.get(x) ?? null),
    }
  })

  return {
    grid: { left: 44, right: 16, top: 30, bottom: 36 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(20, 26, 48, 0.92)',
      borderColor: 'rgba(100, 180, 255, 0.3)',
      textStyle: { color: '#dce4f0', fontSize: 11 },
    },
    legend: {
      top: 4,
      textStyle: { color: 'rgba(200, 220, 255, 0.7)', fontSize: 10 },
    },
    xAxis: {
      type: 'category',
      data: xValues.map(String),
      name: props.xLabel,
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: { color: 'rgba(160, 190, 230, 0.5)', fontSize: 10 },
      axisLine: { lineStyle: { color: 'rgba(100, 180, 255, 0.15)' } },
      axisLabel: { color: 'rgba(160, 190, 230, 0.5)', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      name: props.yLabel,
      nameLocation: 'middle',
      nameGap: 32,
      nameTextStyle: { color: 'rgba(160, 190, 230, 0.5)', fontSize: 10 },
      max: props.yMax ?? null,
      axisLine: { lineStyle: { color: 'rgba(100, 180, 255, 0.15)' } },
      axisLabel: { color: 'rgba(160, 190, 230, 0.5)', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(100, 180, 255, 0.05)' } },
    },
    dataZoom: props.showDataZoom
      ? [
          { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
          {
            type: 'slider',
            xAxisIndex: 0,
            height: 14,
            bottom: 8,
            filterMode: 'none',
            textStyle: { color: 'rgba(160, 190, 230, 0.5)', fontSize: 9 },
          },
        ]
      : [],
    series,
  }
})
</script>

<style scoped>
.bar-chart-wrap {
  width: 100%;
  height: v-bind('height');
}
.bar-chart {
  width: 100%;
  height: 100%;
}
.empty-chart {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(160, 190, 230, 0.3);
  font-size: 12px;
}
</style>
