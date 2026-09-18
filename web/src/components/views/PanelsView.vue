<script setup lang="ts">
import EChart from '../EChart.vue'
import { lineOptions } from '../charts'
import type { LineViewSpec, PanelsViewSpec } from '../../types'
const props = defineProps<{ spec: PanelsViewSpec }>()

function chartSpec(panel: PanelsViewSpec['panels'][number]): LineViewSpec {
  return {
    view: 'line',
    title: panel.name,
    x: props.spec.x,
    series: [{ name: panel.name, data: panel.data }],
    y_name: panel.unit || '',
    connect_nulls: true,
  }
}
</script>

<template>
  <section class="panels">
    <article v-for="(p, i) in props.spec.panels" :key="i" class="panel">
      <header>
        <span>{{ p.name }}</span>
        <small v-if="p.unit">{{ p.unit }}</small>
      </header>
      <EChart :options="lineOptions(chartSpec(p))" height="170px" />
      <p v-if="p.reference">{{ p.reference }}</p>
    </article>
    <footer v-if="props.spec.note">{{ props.spec.note }}</footer>
  </section>
</template>

<style scoped>
.panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
.panel { min-width: 0; background: #191d2b; border: 1px solid var(--border); border-radius: 9px; padding: 8px; }
.panel header { display: flex; align-items: baseline; justify-content: space-between; margin: 2px 4px 4px; }
.panel span { color: var(--text); font-size: 12px; font-weight: 650; }
.panel small { color: var(--text-dim); font-size: 10px; }
.panel p { margin: 2px 4px 4px; color: var(--text-dim); font-size: 10px; line-height: 1.45; }
.panels footer { grid-column: 1 / -1; color: var(--text-dim); font-size: 10px; line-height: 1.5; }
</style>
