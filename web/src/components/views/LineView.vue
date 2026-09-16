<script setup lang="ts">
import EChart from '../EChart.vue'
import { lineOptions } from '../charts'
import type { LineViewSpec } from '../../types'
const props = defineProps<{ spec: LineViewSpec }>()
</script>
<template>
  <EChart :options="lineOptions(props.spec)" :height="props.spec.series.length > 3 ? '320px' : '260px'" />
</template>
