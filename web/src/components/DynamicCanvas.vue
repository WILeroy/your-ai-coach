<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import type { ViewSpec } from '../types'
import LineView from './views/LineView.vue'
import BarView from './views/BarView.vue'
import TableView from './views/TableView.vue'
import MetricCardsView from './views/MetricCardsView.vue'
import SearchResultsView from './views/SearchResultsView.vue'
import InsightView from './views/InsightView.vue'
import PanelsView from './views/PanelsView.vue'
import SessionListView from './views/SessionListView.vue'

const store = useChatStore()
const defaultViews = ref<ViewSpec[]>([])
const loadingDefault = ref(false)

const VIEW_LABELS: Record<string, string> = {
  insight: '决策简报',
  session_list: '训练课',
  line: '趋势图',
  bar: '容量',
  panels: '身体指标',
  table: '数据表',
  search_results: '来源',
  metric_cards: '指标卡',
}

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
watch(() => store.refreshToken, () => {
  if (store.activeViewIndex === -1) loadDefault()
})

const activeView = computed(() =>
  store.activeViewIndex >= 0 ? store.views[store.activeViewIndex] : undefined)
const showing = computed(() => activeView.value ? [activeView.value] : defaultViews.value)
const showDefault = () => store.activeViewIndex === -1
const viewLabel = (v: ViewSpec) => v.title || VIEW_LABELS[v.view] || v.view

function clear() {
  store.showBrief()
  loadDefault() // 刷新默认概览(可能含最新数据)
}
</script>

<template>
  <div class="canvas">
    <div class="canvas-head">
      <span class="dot"></span> 动态画布
      <span class="mode">结论 → 证据 → 数据</span>
      <span v-if="showDefault() && defaultViews.length" class="badge">训练简报</span>
      <span v-else-if="activeView" class="badge active">当前结果</span>
      <button v-if="store.views.length" class="clear-btn" @click="clear">回到概览</button>
    </div>
    <div v-if="store.views.length" class="view-history">
      <button v-for="(v, i) in store.views.slice(0, 8)" :key="v.view + i"
              :class="{ active: i === store.activeViewIndex }" @click="store.selectView(i)">
        {{ i === 0 ? '最新' : i + 1 }} · {{ viewLabel(v) }}
      </button>
      <span class="history-count">保留{{ store.views.length }}/12</span>
    </div>
    <div class="canvas-body">
      <div v-if="!showing.length" class="canvas-empty">
        <div class="empty-icon">📊</div>
        <p>{{ loadingDefault ? '加载今日概览...' : '向教练提问，图表与分析会自动渲染在这里' }}</p>
        <p v-if="!loadingDefault" class="hint">试试「深蹲趋势如何」「本周训练情况」</p>
      </div>
      <div v-for="(v, i) in showing" :key="showDefault() ? 'd' + i : i" class="view-card">
        <div v-if="v.title" class="view-title">{{ v.title }}</div>
        <div v-if="v.window || v.confidence || v.generated_at" class="view-meta">
          <span v-if="v.window">窗口: {{ v.window }}</span>
          <span v-if="v.confidence">证据: {{ v.confidence }}</span>
          <span v-if="v.view !== 'insight' && v.generated_at">更新: {{ v.generated_at.replace('T', ' ') }}</span>
        </div>
        <LineView v-if="v.view === 'line'" :spec="v as any" />
        <BarView v-else-if="v.view === 'bar'" :spec="v as any" />
        <TableView v-else-if="v.view === 'table'" :spec="v as any" />
        <MetricCardsView v-else-if="v.view === 'metric_cards'" :spec="v as any" />
        <SearchResultsView v-else-if="v.view === 'search_results'" :spec="v as any" />
        <InsightView v-else-if="v.view === 'insight'" :spec="v as any" />
        <PanelsView v-else-if="v.view === 'panels'" :spec="v as any" />
        <SessionListView v-else-if="v.view === 'session_list'" :spec="v as any" />
        <p v-if="v.note" class="view-note">{{ v.note }}</p>
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
.canvas-head .mode { font-weight: 400; color: var(--text-dim); opacity: .75; }
.badge {
  font-size: 10px; font-weight: 500; color: var(--blue);
  background: rgba(91,155,213,0.12); padding: 1px 7px; border-radius: 8px;
}
.badge.active { color: var(--orange); background: var(--orange-soft); }
.view-history {
  display: flex; align-items: center; gap: 5px; overflow-x: auto;
  padding: 7px 12px; border-bottom: 1px solid var(--border); background: #141826;
}
.view-history button {
  flex-shrink: 0; background: #1d2233; border: 1px solid var(--border); color: var(--text-dim);
  border-radius: 999px; font-size: 9px; padding: 3px 7px; cursor: pointer;
}
.view-history button.active { color: var(--orange); border-color: var(--orange); background: var(--orange-soft); }
.history-count { flex-shrink: 0; color: var(--text-dim); font-size: 9px; opacity: .65; margin-left: auto; }
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
  padding: 14px; margin-bottom: 12px; scroll-margin-top: 14px;
}
.view-title { font-size: 13px; font-weight: 600; color: var(--text-bright); margin-bottom: 8px; }
.view-meta {
  display: flex; flex-wrap: wrap; gap: 6px; margin: -2px 0 9px;
}
.view-meta span {
  font-size: 9px; color: var(--text-dim); background: #191d2b;
  border: 1px solid var(--border); border-radius: 999px; padding: 2px 6px;
}
.view-note { margin-top: 9px; color: var(--text-dim); font-size: 10px; line-height: 1.5; }
</style>
