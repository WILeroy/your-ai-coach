<script setup lang="ts">
import type { InsightViewSpec } from '../../types'
const props = defineProps<{ spec: InsightViewSpec }>()

const statusClass = () => props.spec.status || 'ready'
const hasEvidence = () => !!props.spec.evidence?.length
const hasCaveats = () => !!(props.spec.caveats?.length || props.spec.readiness_signals?.length)
</script>

<template>
  <section class="brief">
    <header class="brief-head">
      <div>
        <div class="headline">{{ props.spec.headline }}</div>
        <div v-if="props.spec.generated_at" class="meta">生成 {{ props.spec.generated_at.replace('T', ' ') }}</div>
      </div>
      <span class="status" :class="statusClass()">{{ props.spec.status_label || props.spec.status || '信息' }}</span>
    </header>

    <div class="metric-grid">
      <article v-for="(c, i) in props.spec.cards" :key="i" class="metric">
        <div class="metric-top">
          <span>{{ c.label }}</span>
          <small v-if="c.reference">参考 {{ c.reference }}</small>
        </div>
        <strong>{{ c.value ?? '—' }}<em v-if="c.unit">{{ c.unit }}</em></strong>
        <p v-if="c.sub">{{ c.sub }}</p>
      </article>
    </div>

    <div v-if="hasEvidence()" class="evidence">
      <div v-for="(e, i) in props.spec.evidence" :key="i" class="evidence-item">
        <span>{{ e.label }}</span>
        <strong>{{ e.value }}</strong>
        <p>{{ e.detail }}</p>
      </div>
    </div>

    <footer v-if="hasCaveats()" class="caveats">
      <p v-for="(s, i) in props.spec.readiness_signals" :key="'s' + i">⚠ {{ s }}</p>
      <p v-for="(c, i) in props.spec.caveats" :key="'c' + i">· {{ c }}</p>
    </footer>
  </section>
</template>

<style scoped>
.brief { display: flex; flex-direction: column; gap: 12px; }
.brief-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.headline { font-size: 16px; line-height: 1.35; color: var(--text-bright); font-weight: 700; }
.meta { margin-top: 3px; font-size: 10px; color: var(--text-dim); }
.status { flex-shrink: 0; border-radius: 999px; padding: 4px 9px; font-size: 10px; font-weight: 700; }
.status.ready { color: #56b881; background: rgba(86,184,129,.12); }
.status.caution { color: #e8d26a; background: rgba(232,210,106,.12); }
.status.limited { color: #5b9bd5; background: rgba(91,155,213,.12); }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 8px; }
.metric { min-width: 0; background: #191d2b; border: 1px solid var(--border); border-radius: 9px; padding: 10px; }
.metric-top { display: flex; justify-content: space-between; gap: 6px; color: var(--text-dim); font-size: 10px; }
.metric-top small { opacity: .75; text-align: right; }
.metric strong { display: block; margin-top: 6px; color: var(--text-bright); font-size: 20px; line-height: 1; }
.metric em { margin-left: 3px; font-size: 11px; font-style: normal; color: var(--text-dim); }
.metric p { margin: 6px 0 0; color: var(--text-dim); font-size: 10px; line-height: 1.45; }
.evidence { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; }
.evidence-item { border-left: 2px solid #3a4256; padding-left: 8px; }
.evidence-item span { display: block; font-size: 10px; color: var(--text-dim); }
.evidence-item strong { display: block; margin: 2px 0; color: var(--text); font-size: 12px; }
.evidence-item p { margin: 0; color: var(--text-dim); font-size: 10px; line-height: 1.5; }
.caveats { border-top: 1px dashed var(--border); padding-top: 8px; }
.caveats p { margin: 3px 0; color: var(--text-dim); font-size: 10px; line-height: 1.5; }
</style>
