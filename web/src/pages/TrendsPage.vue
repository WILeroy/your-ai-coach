<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useSummaryStore } from '../stores/summary'
import EChart from '../components/EChart.vue'
import { lineOptions, barOptions } from '../components/charts'

const store = useSummaryStore()
onMounted(() => store.load())

const e1rmOptions = computed(() => {
  const e: Record<string, any[]> = store.data?.e1rm || {}
  const items = Object.entries(e).filter(([, v]) => v.length > 0).slice(0, 6)
  const dates = Array.from(new Set(items.flatMap(([, v]) => v.map(x => x.date)))).sort()
  return lineOptions({
    view: 'line', title: '', x: dates,
    series: items.map(([name, v]) => {
      const m: Record<string, number> = {}
      v.forEach(x => { m[x.date] = x.e1rm })
      return { name, data: dates.map(d => m[d] ?? null) }
    }),
    y_name: 'kg', connect_nulls: true,
  })
})

const volumeOptions = computed(() => {
  const vol: Record<string, Record<string, number>> = store.data?.volume || {}
  const weeks = Object.keys(vol).slice(-16)
  const patterns = ['squat', 'hinge', 'push', 'pull', 'core', 'other']
  const PATTERN_CN: Record<string, string> = { squat: '蹲', hinge: '铰链', push: '推', pull: '拉', core: '核心', other: '其他' }
  return barOptions({
    view: 'bar', title: '', x: weeks,
    series: patterns.filter(p => weeks.some(w => (vol[w][p] || 0) > 0))
      .map(p => ({ name: PATTERN_CN[p] || p, data: weeks.map(w => vol[w][p] || 0) })),
    y_name: 'kg', stack: true,
  })
})

const acwrOptions = computed(() => {
  const hist = store.data?.acwr_history || []
  return lineOptions({
    view: 'line', title: '', x: hist.map((h: any) => h.date),
    series: [{ name: 'ACWR', data: hist.map((h: any) => h.ratio) }],
    mark_line: 1.5,
  })
})

const runningOptions = computed(() => {
  const t: any[] = store.data?.running_trend || []
  return lineOptions({
    view: 'line', title: '', x: t.map(x => x.date),
    series: [{ name: '配速', data: t.map(x => x.pace_sec) }],
    y_name: 'sec/km',
  })
})
</script>

<template>
  <div class="page-pad">
    <h2 class="page-title">趋势</h2>
    <div v-if="store.loading && !store.data" class="loading">加载中...</div>
    <template v-if="store.data">
      <div class="card"><h3>三大项 e1RM 趋势</h3>
        <EChart :options="e1rmOptions" height="340px" /></div>
      <div class="card"><h3>周容量吨位</h3>
        <EChart :options="volumeOptions" height="300px" /></div>
      <div class="card"><h3>急慢性负荷比 ACWR</h3>
        <EChart v-if="(store.data.acwr_history || []).length > 1" :options="acwrOptions" height="280px" />
        <div v-else class="hint">需要 3-4 周数据</div></div>
      <div class="card"><h3>跑步经济性 (LSD 配速)</h3>
        <EChart v-if="(store.data.running_trend || []).length > 1" :options="runningOptions" height="280px" />
        <div v-else class="hint">暂无 LSD 记录</div></div>
    </template>
  </div>
</template>

<style scoped>
.page-title { font-size: 18px; color: var(--text-bright); margin-bottom: 16px; }
.loading { color: var(--text-dim); text-align: center; padding: 60px; }
.hint { color: var(--text-dim); font-size: 12px; padding: 8px 0; }
</style>
