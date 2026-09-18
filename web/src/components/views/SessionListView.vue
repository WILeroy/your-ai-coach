<script setup lang="ts">
import type { SessionListViewSpec } from '../../types'
const props = defineProps<{ spec: SessionListViewSpec }>()

const typeLabel: Record<string, string> = {
  legs: '腿', push: '推', pull: '拉', interval: '间歇',
  lsd: '有氧', relax: '恢复', rest: '休息',
}
const label = (v: string) => typeLabel[v] || v
</script>

<template>
  <section class="sessions">
    <div v-if="!props.spec.sessions?.length" class="empty">暂无训练课</div>
    <article v-for="s in props.spec.sessions" :key="s.id" class="session" :data-tone="s.tone">
      <header>
        <div>
          <strong>{{ s.date.slice(5) }}</strong>
          <span class="type">{{ label(s.type) }}</span>
        </div>
        <span class="status">{{ s.status_label }}</span>
      </header>

      <div class="tracks">
        <section class="track planned">
          <header>计划 <small>{{ s.planned.length || 0 }}项</small></header>
          <ul v-if="s.planned.length">
            <li v-for="x in s.planned" :key="x">{{ x }}</li>
          </ul>
          <p v-else class="muted">无逐项计划</p>
          <p v-if="s.planned_notes" class="note">{{ s.planned_notes }}</p>
        </section>

        <section class="track actual">
          <header>实际 <small>{{ s.record_state }}</small></header>
          <ul v-if="s.actual.length">
            <li v-for="x in s.actual" :key="x">{{ x }}</li>
          </ul>
          <p v-else class="muted">未录入逐组/实际数据</p>
          <p v-if="s.actual_notes" class="note">{{ s.actual_notes }}</p>
        </section>
      </div>

      <footer v-if="s.rpe != null">课后RPE {{ s.rpe }}/10</footer>
    </article>
  </section>
</template>

<style scoped>
.sessions { display: flex; flex-direction: column; gap: 10px; }
.empty { color: var(--text-dim); text-align: center; padding: 24px; font-size: 13px; }
.session { border: 1px solid var(--border); border-radius: 10px; background: #191d2b; overflow: hidden; }
.session header { display: flex; justify-content: space-between; align-items: center; padding: 9px 11px; background: #141826; }
.session strong { color: var(--text-bright); font-size: 13px; margin-right: 7px; }
.type { color: var(--text-dim); font-size: 11px; }
.status { border-radius: 999px; padding: 3px 8px; font-size: 10px; font-weight: 700; }
.session[data-tone=ok] .status { color: #56b881; background: rgba(86,184,129,.12); }
.session[data-tone=warn] .status { color: #e8d26a; background: rgba(232,210,106,.12); }
.session[data-tone=info] .status { color: #5b9bd5; background: rgba(91,155,213,.12); }
.session[data-tone=muted] .status { color: var(--text-dim); background: #232736; }
.tracks { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.track { padding: 9px 11px; border-right: 1px solid var(--border); }
.track:last-child { border-right: none; }
.track header { display: flex; justify-content: space-between; color: var(--text-dim); font-size: 10px; background: transparent; padding: 0 0 5px; }
.track ul { margin: 0; padding-left: 16px; }
.track li { color: var(--text); font-size: 11px; line-height: 1.55; }
.muted, .note { margin: 0; color: var(--text-dim); font-size: 10px; line-height: 1.5; }
.note { margin-top: 5px; }
.session footer { padding: 6px 11px 8px; color: var(--text-dim); font-size: 10px; border-top: 1px dashed var(--border); }
</style>
