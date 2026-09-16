<script setup lang="ts">
defineProps<{
  spec: { view: string; title?: string; results: { title: string; url: string; snippet: string }[] }
}>()
function host(u: string) {
  try { return new URL(u).hostname.replace(/^www\./, '') } catch { return '' }
}
</script>
<template>
  <div class="sr-list">
    <a v-for="(r, i) in spec.results" :key="i" :href="r.url" target="_blank" rel="noopener" class="sr-item">
      <div class="sr-head">
        <span class="sr-title">{{ r.title }}</span>
        <span class="sr-host">{{ host(r.url) }}</span>
      </div>
      <div v-if="r.snippet" class="sr-snippet">{{ r.snippet }}</div>
    </a>
  </div>
</template>
<style scoped>
.sr-list { display: flex; flex-direction: column; gap: 8px; }
.sr-item {
  display: block; text-decoration: none; background: #1a1e2b;
  border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
  transition: border-color 0.15s;
}
.sr-item:hover { border-color: var(--orange); }
.sr-head { display: flex; align-items: baseline; gap: 8px; }
.sr-title { font-size: 13px; font-weight: 600; color: var(--blue); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sr-host { font-size: 10px; color: var(--text-dim); margin-left: auto; flex-shrink: 0; }
.sr-snippet { font-size: 11px; color: var(--text-dim); margin-top: 3px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
