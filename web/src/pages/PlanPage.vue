<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useSummaryStore } from '../stores/summary'

const store = useSummaryStore()
onMounted(() => store.load())

const TYPE_CN: Record<string, string> = {
  legs: '下肢', push: '推', pull: '拉', interval: '间歇', lsd: 'LSD', relax: '放松', rest: '休息',
}
const today = new Date().toISOString().slice(0, 10)

const cycle = computed(() => store.data?.cycles?.[0] || null)
const weeks = computed(() => {
  if (!cycle.value) return []
  const byWeek: Record<number, any[]> = {}
  for (const s of cycle.value.sessions || []) {
    const w = s.week_no || 1
    ;(byWeek[w] = byWeek[w] || []).push(s)
  }
  return Object.entries(byWeek).map(([w, days]) => ({ week: +w, days }))
})
</script>

<template>
  <div class="page-pad">
    <h2 class="page-title">周期计划</h2>
    <div v-if="store.loading && !store.data" class="loading">加载中...</div>
    <template v-if="cycle">
      <div class="card cycle-head">
        <div class="c-title">{{ cycle.start_date }} → {{ cycle.end_date }}</div>
        <span class="type-badge t-interval">{{ cycle.phase }}</span>
      </div>

      <div v-if="cycle.goals?.length" class="card">
        <h3>验收目标</h3>
        <div class="goals">
          <div v-for="(g, i) in cycle.goals" :key="i" class="goal">
            <span class="g-ex">{{ g.exercise }}</span>
            <span class="g-prog">{{ g.from }}kg → <b>{{ g.to }}kg</b> · {{ g.sets }}×{{ g.reps }}</span>
          </div>
        </div>
      </div>

      <div v-for="w in weeks" :key="w.week" class="card">
        <h3>第 {{ w.week }} 周</h3>
        <div class="week-grid">
          <div v-for="d in w.days" :key="d.date" class="day" :class="{
            today: d.date === today,
            done: d.status === 'done', skipped: d.status === 'skipped',
          }">
            <div class="d-top">
              <span class="d-date">{{ d.date.slice(5) }}</span>
              <span class="type-badge" :class="'t-' + d.type">{{ TYPE_CN[d.type] || d.type }}</span>
            </div>
            <div class="d-note">{{ d.notes || '' }}</div>
            <div class="d-status">{{ d.status }}</div>
          </div>
        </div>
      </div>
    </template>
    <div v-else-if="store.data" class="card hint">暂无周期计划。可以在教练页让 FIT 帮你生成：「帮我生成一个4周的训练周期」</div>
  </div>
</template>

<style scoped>
.page-title { font-size: 18px; color: var(--text-bright); margin-bottom: 16px; }
.loading { color: var(--text-dim); text-align: center; padding: 60px; }
.cycle-head { display: flex; align-items: center; gap: 12px; }
.c-title { font-size: 15px; font-weight: 700; color: var(--text-bright); font-family: monospace; }
.goals { display: flex; flex-direction: column; gap: 6px; }
.goal { display: flex; justify-content: space-between; font-size: 13px; flex-wrap: wrap; gap: 6px; }
.g-ex { color: var(--text-bright); }
.g-prog b { color: var(--orange); }
.week-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.day {
  background: #1a1e2b; border: 1px solid var(--border); border-radius: 8px; padding: 10px;
}
.day.today { border-color: var(--orange); }
.day.done { border-left: 3px solid var(--green); }
.day.skipped { opacity: 0.55; }
.d-top { display: flex; justify-content: space-between; align-items: center; }
.d-date { font-size: 11px; color: var(--text-dim); font-family: monospace; }
.d-note { font-size: 11px; color: var(--text); margin: 6px 0 4px; min-height: 18px; }
.d-status { font-size: 10px; color: var(--text-dim); text-transform: uppercase; }
.hint { color: var(--text-dim); font-size: 13px; }
</style>
