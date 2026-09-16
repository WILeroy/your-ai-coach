<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../api/client'

const review = ref<any>(null)
const weekStart = ref('')
const loading = ref(false)

function thisMonday() {
  const d = new Date()
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  return d.toISOString().slice(0, 10)
}

async function load(week?: string) {
  loading.value = true
  try {
    review.value = await api.get('/api/review' + (week ? `?week=${week}` : ''))
    weekStart.value = review.value.week_start || thisMonday()
  } catch (e: any) {
    review.value = { error: e.message }
  } finally {
    loading.value = false
  }
}

function nav(delta: number) {
  const d = new Date(weekStart.value)
  d.setDate(d.getDate() + delta * 7)
  load(d.toISOString().slice(0, 10))
}

onMounted(() => load())

function fmtPace(sec: number | null) {
  if (!sec) return '—'
  return `${Math.floor(sec / 60)}'${String(Math.round(sec % 60)).padStart(2, '0')}`
}
</script>

<template>
  <div class="page-pad">
    <div class="head">
      <h2 class="page-title">周报</h2>
      <div class="nav">
        <button @click="nav(-1)">‹ 上一周</button>
        <span class="week">{{ weekStart }}</span>
        <button @click="nav(1)">下一周 ›</button>
      </div>
    </div>
    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="review?.error" class="card err">{{ review.error }}</div>
    <template v-else-if="review">
      <div class="metrics">
        <div class="card m"><div class="m-val">{{ review.compliance ?? '—' }}%</div><div class="m-l">计划完成度</div></div>
        <div class="card m"><div class="m-val">{{ review.total_sessions ?? 0 }}</div><div class="m-l">训练次数</div></div>
        <div class="card m"><div class="m-val">{{ review.total_tonnage ?? 0 }}<span class="u">kg</span></div><div class="m-l">总容量</div></div>
        <div class="card m"><div class="m-val">{{ review.avg_rpe ?? '—' }}</div><div class="m-l">平均 RPE</div></div>
      </div>

      <div v-if="review.alerts?.length" class="card">
        <h3>⚠️ 监控与警报</h3>
        <ul class="alerts">
          <li v-for="(a, i) in review.alerts" :key="i">{{ a }}</li>
        </ul>
      </div>

      <div v-if="review.days?.length" class="card">
        <h3>每日明细</h3>
        <div class="days">
          <div v-for="d in review.days" :key="d.date" class="day-row">
            <span class="d-date">{{ d.date.slice(5) }}</span>
            <span class="type-badge" :class="'t-' + d.type">{{ d.type }}</span>
            <span class="d-note">{{ d.summary || d.notes || '' }}</span>
          </div>
        </div>
      </div>

      <div v-if="review.highlights?.length" class="card">
        <h3>💪 本周亮点</h3>
        <ul class="hl">
          <li v-for="(h, i) in review.highlights" :key="i">{{ typeof h === 'string' ? h : JSON.stringify(h) }}</li>
        </ul>
      </div>
    </template>
  </div>
</template>

<style scoped>
.head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.page-title { margin: 0; }
.nav { display: flex; align-items: center; gap: 10px; }
.nav button {
  background: var(--card); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 5px 12px; font-size: 12px; cursor: pointer;
}
.nav button:hover { border-color: var(--orange); }
.week { font-size: 13px; color: var(--text-bright); font-weight: 600; font-family: monospace; }
.loading { color: var(--text-dim); text-align: center; padding: 60px; }
.err { color: var(--red); }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.m { text-align: center; }
.m-val { font-size: 24px; font-weight: 700; color: var(--text-bright); }
.u { font-size: 12px; color: var(--text-dim); margin-left: 2px; }
.m-l { font-size: 11px; color: var(--text-dim); }
.alerts { padding-left: 18px; font-size: 13px; }
.alerts li { margin: 4px 0; color: var(--orange); }
.days { display: flex; flex-direction: column; gap: 6px; }
.day-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.d-date { color: var(--text-dim); font-family: monospace; min-width: 42px; }
.d-note { color: var(--text); }
.hl { padding-left: 18px; font-size: 13px; }
.hl li { margin: 4px 0; }
</style>
