<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ options: echarts.EChartsOption; height?: string }>()

const el = ref<HTMLElement>()
let chart: echarts.ECharts | null = null
let resizeObs: ResizeObserver | null = null

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value, undefined, { renderer: 'canvas' })
  chart.setOption(props.options)
  resizeObs = new ResizeObserver(() => chart?.resize())
  resizeObs.observe(el.value)
})

watch(() => props.options, (v) => chart?.setOption(v, true), { deep: true })

onUnmounted(() => {
  resizeObs?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div ref="el" :style="{ width: '100%', height: height || '300px' }"></div>
</template>
