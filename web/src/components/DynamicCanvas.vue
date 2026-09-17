<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useChatStore } from '../stores/chat'
import type { ViewSpec } from '../types'
import LineView from './views/LineView.vue'
import BarView from './views/BarView.vue'
import TableView from './views/TableView.vue'
import MetricCardsView from './views/MetricCardsView.vue'
import SearchResultsView from './views/SearchResultsView.vue'

const store = useChatStore()
const defaultViews = ref<ViewSpec[]>([])
const loadingDefault = ref(false)

async function loadDefault() {
  loadingDefault.value = true
  try {
    const r = await fetch('/api/canvas/default')
    if (r.ok) {
      const d = await r.json()
      defaultViews.value = d.views || []
    }
  } catch {}
  loadingDefault.value = false
}

onMounted(loadDefault)

const showing = computed(() => store.views.length ? store.views : defaultViews.value)
const showDefault = () => !store.views.length

function clear() {
  store.views = []
  loadDefault() // 刷新默认概览(可能含最新数据)
}
</script>

<template>
  <div class="canvas">
    <div class="canvas-head">
      <span class="dot"></span> 动态画布
      <span v-if="showDefault() && defaultViews.length" class="badge">默认概览</span>
      <button v-if="store.views.length" class="clear-btn" @click="clear">回到概览</button>
    </div>
    <div class="canvas-body">
      <div v-if="!showing.length" class="canvas-empty">
        <div class="empty-icon">📊</div>
        <p>{{ loadingDefault ? '加载今日概览...' : '向教练提问，图表与分析会自动渲染在这里' }}</p>
        <p v-if="!loadingDefault" class="hint">试试「深蹲趋势如何」「本周训练情况」</p>
      </div>
      <div v-for="(v, i) in showing" :key="showDefault() ? 'd' + i : i" class="view-card">
        <div v-if="v.title" class="view-title">{{ v.title }}</div>
        <LineView v-if="v.view === 'line'" :spec="v as any" />
        <BarView v-else-if="v.view === 'bar'" :spec="v as any" />
        <TableView v-else-if="v.view === 'table'" :spec="v as any" />
        <MetricCardsView v-else-if="v.view === 'metric_cards'" :spec="v as any" />
        <SearchResultsView v-else-if="v.view === 'search_results'" :spec="v as any" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.canvas {
  height: 100%; display: flex; flex-direction: column;
  background: var(--bg-deep); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden;
}
.canvas-head {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
  font-size: 12px; font-weight: 600; color: var(--text-dim);
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); }
.badge {
  font-size: 10px; font-weight: 500; color: var(--blue);
  background: rgba(91,155,213,0.12); padding: 1px 7px; border-radius: 8px;
}
.clear-btn {
  margin-left: auto; background: none; border: none; color: var(--text-dim);
  font-size: 11px; cursor: pointer; padding: 2px 6px; border-radius: 6px;
}
.clear-btn:hover { color: var(--orange); }
.canvas-body { flex: 1; overflow-y: auto; padding: 14px; }
.canvas-empty {
  height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: var(--text-dim); font-size: 13px; text-align: center; gap: 6px;
}
.empty-icon { font-size: 32px; opacity: 0.5; }
.hint { font-size: 11px; opacity: 0.6; }
.view-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px; margin-bottom: 12px;
}
.view-title { font-size: 13px; font-weight: 600; color: var(--text-bright); margin-bottom: 8px; }
</style>
