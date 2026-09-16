<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useSummaryStore } from '../stores/summary'
import { useChatStore } from '../stores/chat'
import EChart from '../components/EChart.vue'
import { lineOptions, COLORS } from '../components/charts'

const store = useSummaryStore()
const chat = useChatStore()
onMounted(() => store.load())

// agent 写入后自动刷新
watch(() => chat.sessionId, () => store.refresh())

const TYPE_CN: Record<string, string> = {
  legs: '下肢', push: '推', pull: '拉', interval: '间歇', lsd: 'LSD', relax: '放松', rest: '休息',
}
const today = new Date().toISOString().slice(0, 10)

const cycle = computed(() => store.data?.cycles?.[0] || null)
const weekSessions = computed(() => {
  const ss = cycle.value?.sessions || []
  const now = new Date()
  const monday = new Date(now)
  monday.setDate(now.getDate() - ((now.getDay() + 6) % 7))
  const mon = monday.toISOString().slice(0, 10)
  const sun = new Date(monday); sun.setDate(monday.getDate() + 6)
  const sunS = sun.toISOString().slice(0, 10)
  return ss.filter((s: any) => s.date >= mon && s.date <= sunS)
})
const todaySession = computed(() => weekSessions.value.find((s: any) => s.date === today))
const todaySets = computed(() => (todaySession.value?.sets_json ? JSON.parse(todaySession.value.sets_json) : []))

const acwr = computed(() => store.data?.acwr)
const acwrStatus = computed(() => {
  if (!acwr.value) return { label: '数据不足', cls: 't-rest' }
  const r = acwr.value.ratio
  if (r > 1.5) return { label: '偏高 ⚠️', cls: 't-push' }
  if (r < 0.8) return { label: '偏低', cls: 't-interval' }
  return { label: '正常', cls: 't-relax' }
})

const e1rmCards = computed(() => {
  const e: Record<string, any[]> = store.data?.e1rm || {}
  return Object.entries(e)
    .filter(([, v]) => v.length > 0)
    .map(([name, entries]) => {
      const last = entries[entries.length - 1]
      const prev = entries.length > 1 ? entries[entries.length - 2] : null
      const delta = prev ? +(last.e1rm - prev.e1rm).toFixed(1) : null
      return { name, e1rm: last.e1rm, kg: last.kg, reps: last.reps, date: last.date, delta }
    }).slice(0, 6)
})

const acwrOptions = computed(() => {
  const hist = (store.data?.acwr_history || []).slice(-16)
  return lineOptions({
    view: 'line', title: '', x: hist.map((h: any) => h.date),
    series: [{ name: 'ACWR', data: hist.map((h: any) => h.ratio) }],
    y_name: '', mark_line: 1.5,
  })
})
</script>

<template>
  <div class="page-pad">
    <h2 class="page-title">仪表盘</h2>
    <div v-if="store.loading && !store.data" class="loading">加载中...</div>
    <template v-if="store.data">
      <!-- 今日训练 -->
      <div class="card">
        <div class="today-head">
          <h3>今日训练 · {{ new Date().toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' }) }}</h3>
          <span v-if="todaySession" class="type-badge" :class="'t-' + todaySession.type">
            {{ TYPE_CN[todaySession.type] || todaySession.type }}
          </span>
        </div>
        <div v-if="todaySession" class="today-note">{{ todaySession.notes || '' }}</div>
        <div v-else class="today-rest">今天是休息日 💤</div>
      </div>

      <!-- 周历 -->
      <div class="section-label">本周</div>
      <div class="week-strip">
        <div v-for="s in weekSessions" :key="s.date" class="day"
          :class="{ today: s.date === today, done: s.status === 'done' }">
          <div class="d-date">{{ s.date.slice(5) }}</div>
          <div class="d-type type-badge" :class="'t-' + s.type">{{ TYPE_CN[s.type] || s.type }}</div>
          <div class="d-status">{{ s.status === 'done' ? '✓' : s.status === 'planned' ? '·' : s.status }}</div>
        </div>
        <div v-if="!weekSessions.length" class="no-week">本周暂无计划</div>
      </div>

      <!-- e1RM -->
      <div class="section-label">三大项 e1RM</div>
      <div class="e1rm-grid">
        <div v-for="c in e1rmCards" :key="c.name" class="card e1rm-card">
          <div class="e-name">{{ c.name }}</div>
          <div class="e-val">{{ c.e1rm }}<span class="e-unit">kg</span></div>
          <div class="e-sub">{{ c.kg }}kg × {{ c.reps }} · {{ c.date.slice(5) }}</div>
          <div v-if="c.delta !== null" class="e-delta" :class="(c.delta ?? 0) >= 0 ? 'up' : 'down'">
            {{ (c.delta ?? 0) >= 0 ? '↑' : '↓' }} {{ Math.abs(c.delta ?? 0) }}kg
          </div>
          <div v-else class="e-delta new">NEW</div>
        </div>
      </div>

      <!-- ACWR -->
      <div class="section-label">负荷状态 (ACWR)</div>
      <div class="card">
        <div class="acwr-head">
          <span class="acwr-val">{{ acwr ? acwr.ratio : '—' }}</span>
          <span class="type-badge" :class="acwrStatus.cls">{{ acwrStatus.label }}</span>
        </div>
        <EChart v-if="(store.data?.acwr_history || []).length > 1" :options="acwrOptions" height="200px" />
        <div v-else class="acwr-hint">需要 3-4 周训练数据计算急慢性负荷比</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page-title { font-size: 18px; color: var(--text-bright); margin-bottom: 16px; }
.loading { color: var(--text-dim); text-align: center; padding: 60px; }
.today-head { display: flex; align-items: center; gap: 10px; }
.today-head h3 { margin: 0; }
.today-note { color: var(--text); font-size: 13px; margin-top: 8px; }
.today-rest { color: var(--text-dim); padding: 10px 0; }
.week-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; }
.day {
  min-width: 86px; background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px; text-align: center;
}
.day.today { border-color: var(--orange); box-shadow: 0 0 12px rgba(245,158,66,0.12); }
.day.done { border-left: 3px solid var(--green); }
.d-date { font-size: 10px; color: var(--text-dim); }
.d-type { margin: 4px 0; }
.d-status { font-size: 11px; color: var(--green); }
.no-week { color: var(--text-dim); font-size: 12px; padding: 12px; }
.e1rm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }
.e1rm-card { margin-bottom: 0; }
.e-name { font-size: 12px; color: var(--text-dim); }
.e-val { font-size: 26px; font-weight: 700; color: var(--text-bright); }
.e-unit { font-size: 12px; color: var(--text-dim); margin-left: 3px; }
.e-sub { font-size: 11px; color: var(--text-dim); }
.e-delta { font-size: 11px; margin-top: 4px; }
.e-delta.up { color: var(--green); }
.e-delta.down { color: var(--red); }
.e-delta.new { color: var(--blue); }
.acwr-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.acwr-val { font-size: 24px; font-weight: 700; color: var(--text-bright); }
.acwr-hint { color: var(--text-dim); font-size: 12px; padding: 10px 0; }
</style>
