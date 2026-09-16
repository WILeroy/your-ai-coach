<script setup lang="ts">
import { useChatStore } from '../stores/chat'
import LineView from './views/LineView.vue'
import BarView from './views/BarView.vue'
import TableView from './views/TableView.vue'
import MetricCardsView from './views/MetricCardsView.vue'
import SearchResultsView from './views/SearchResultsView.vue'
import UnknownView from './views/UnknownView.vue'

const store = useChatStore()
</script>

<template>
  <div class="canvas">
    <div class="canvas-head">
      <span class="dot"></span> 动态画布
      <button v-if="store.views.length" class="clear-btn" @click="store.views = []">清空</button>
    </div>
    <div class="canvas-body">
      <div v-if="!store.views.length" class="canvas-empty">
        <div class="empty-icon">📊</div>
        <p>向教练提问，图表与分析会自动渲染在这里</p>
        <p class="hint">试试「深蹲趋势如何」「本周训练情况」</p>
      </div>
      <transition-group name="fade">
        <div v-for="(v, i) in store.views" :key="i" class="view-card">
          <div v-if="v.title" class="view-title">{{ v.title }}</div>
          <LineView v-if="v.view === 'line'" :spec="v as any" />
          <BarView v-else-if="v.view === 'bar'" :spec="v as any" />
          <TableView v-else-if="v.view === 'table'" :spec="v as any" />
          <MetricCardsView v-else-if="v.view === 'metric_cards'" :spec="v as any" />
          <SearchResultsView v-else-if="v.view === 'search_results'" :spec="v as any" />
          <UnknownView v-else :spec="v as any" />
        </div>
      </transition-group>
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
.clear-btn {
  margin-left: auto; background: none; border: none; color: var(--text-dim);
  font-size: 11px; cursor: pointer; padding: 2px 6px; border-radius: 6px;
}
.clear-btn:hover { color: var(--red); }
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
