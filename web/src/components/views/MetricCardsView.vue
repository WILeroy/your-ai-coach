<script setup lang="ts">
import type { MetricCardsSpec } from '../../types'
const props = defineProps<{ spec: MetricCardsSpec }>()
</script>
<template>
  <div class="mc-grid">
    <div v-for="(c, i) in props.spec.cards" :key="i" class="mc-card">
      <div class="mc-value">{{ c.value }}<span v-if="c.unit" class="mc-unit">{{ c.unit }}</span></div>
      <div class="mc-label">{{ c.label }}</div>
    </div>
  </div>
</template>
<style scoped>
.mc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; }
.mc-card {
  background: #1a1e2b; border: 1px solid var(--border); border-radius: 10px;
  padding: 12px; text-align: center;
}
.mc-value { font-size: 20px; font-weight: 700; color: var(--text-bright); }
.mc-unit { font-size: 11px; color: var(--text-dim); margin-left: 3px; }
.mc-label { font-size: 10px; color: var(--text-dim); margin-top: 3px; }
</style>
