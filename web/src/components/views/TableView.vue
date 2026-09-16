<script setup lang="ts">
import type { TableViewSpec } from '../../types'
const props = defineProps<{ spec: TableViewSpec }>()
</script>
<template>
  <div class="tv-wrap">
    <div v-if="!props.spec.rows?.length" class="tv-empty">暂无数据</div>
    <div v-else class="tv-scroll">
      <table>
        <thead>
          <tr><th v-for="c in props.spec.columns" :key="c.key">{{ c.label }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in props.spec.rows" :key="i">
            <td v-for="c in props.spec.columns" :key="c.key">{{ r[c.key] ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
<style scoped>
.tv-wrap { width: 100%; }
.tv-empty { color: var(--text-dim); text-align: center; padding: 24px; font-size: 13px; }
.tv-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; padding: 7px 10px; color: var(--text-dim); font-size: 11px; background: #1a1e2b; white-space: nowrap; }
td { padding: 6px 10px; border-top: 1px solid var(--border); white-space: nowrap; }
tbody tr:hover td { background: rgba(255,255,255,0.02); }
</style>
