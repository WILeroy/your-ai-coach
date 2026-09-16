<script setup lang="ts">
import EChart from '../EChart.vue'
import { barOptions } from '../charts'
import type { BarViewSpec } from '../../types'
const props = defineProps<{ spec: BarViewSpec }>()
</script>
<template>
  <EChart :options="barOptions(props.spec)" height="280px" />
</template>
