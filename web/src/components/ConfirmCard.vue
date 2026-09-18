<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'
import type { PendingConfirm } from '../types'

const props = defineProps<{ confirm: PendingConfirm }>()
const store = useChatStore()

const TOOL_LABELS: Record<string, string> = {
  log_training: '📝 记录训练', update_session: '✏️ 修改训练', delete_data: '🗑️ 删除数据',
  adjust_plan: '📋 调整计划', create_plan: '🔄 生成周期', manage_exercises: '🏋️ 动作库变更',
  log_body_metric: '📊 记录身体数据',
}

interface ActionItem { tool: string; preview: Record<string, unknown> }

const FIELD_LABELS: Record<string, string> = {
  weight: '体重',
  sleep_h: '睡眠',
  resting_hr: '静息心率',
  hrv_ms: 'HRV(ms)',
}

const items = computed<ActionItem[]>(() => {
  if (props.confirm.actions?.length) return props.confirm.actions
  return [{ tool: props.confirm.tool, preview: props.confirm.preview }]
})

const multi = computed(() => items.value.length > 1)

function rows(preview: Record<string, unknown>): [string, string][] {
  const out: [string, string][] = []
  if (preview.merge_rule && preview.existing && preview.result) {
    const old = preview.existing as Record<string, unknown>
    const result = preview.result as Record<string, unknown>
    const changedFields = new Set(preview.changed_fields as string[] || [])
    for (const [field, label] of [
      ['weight', '体重'], ['sleep_h', '睡眠'],
      ['resting_hr', '静息心率'], ['hrv_ms', 'HRV'],
    ] as [string, string][]) {
      if (changedFields.has(field)) {
        out.push([label, `${old[field] ?? '—'} → ${result[field] ?? '—'}（本次变更）`])
      }
    }
    if (changedFields.has('notes')) out.push(['备注', '（本次变更）'])
    out.push(['本次变更', `${changedFields.size}项`])
    out.push(['合并规则', String(preview.merge_rule)])
    return out
  }
  for (const [k, v] of Object.entries(preview || {})) {
    if (k === 'sets_summary' && Array.isArray(v)) {
      out.push(['动作', v.map((s: any) => {
        const g = (s.groups || []).join(' / ')
        const name = s.input_name && s.input_name !== s.exercise ? `${s.input_name} → ${s.exercise}` : s.exercise
        const mark = s.exists_in_db === false ? '（确认后自动新增）'
          : s.resolution === 'alias' ? '（别名归并）'
          : s.resolution === 'fuzzy' ? '（高置信匹配）' : ''
        return `${name}: ${g}${mark}`
      }).join('\n')])
    } else if (k === 'new_exercises' && Array.isArray(v)) {
      out.push(['自动新增', v.map((m: any) => `${m.name} / ${m.suggested_pattern || 'accessory'}`).join('、')])
    } else if (k === 'missing_exercises') {
      // new_exercises 已经以“确认后自动新增”展示，避免重复/误导。
      continue
    } else if (typeof v === 'object' && v !== null) {
      out.push([k, JSON.stringify(v)])
    } else if (v !== null && v !== undefined && v !== '') {
      out.push([FIELD_LABELS[k] || k, String(v)])
    }
  }
  return out
}
</script>

<template>
  <div class="confirm-card" :class="{ done: confirm.done }">
    <div class="head">
      {{ multi ? `⚡ 批量操作（${items.length} 项）` : TOOL_LABELS[confirm.tool] || confirm.tool }}
    </div>

    <div v-for="(a, i) in items" :key="i" class="action-block" :class="{ multi }">
      <div v-if="multi" class="action-title">{{ i + 1 }}. {{ TOOL_LABELS[a.tool] || a.tool }}</div>
      <div class="rows">
        <div v-for="([k, v], j) in rows(a.preview)" :key="j" class="row">
          <span class="k">{{ k }}</span>
          <span class="v" style="white-space: pre-line">{{ v }}</span>
        </div>
      </div>
    </div>

    <div class="actions" v-if="!confirm.done">
      <button class="btn yes" :disabled="store.sending" @click="store.confirm(confirm.action_id, true)">
        {{ multi ? `确认全部 (${items.length})` : '确认执行' }}
      </button>
      <button class="btn no" :disabled="store.sending" @click="store.confirm(confirm.action_id, false)">
        {{ multi ? '全部取消' : '取消' }}
      </button>
    </div>
    <div v-else class="done-mark">已处理 ✓</div>
  </div>
</template>

<style scoped>
.confirm-card {
  margin-top: 8px; background: #131620; border: 1px solid #3a3320;
  border-radius: 10px; padding: 12px;
}
.confirm-card.done { opacity: 0.6; }
.head { font-size: 13px; font-weight: 600; color: var(--orange); margin-bottom: 8px; }
.action-block.multi { border-top: 1px dashed var(--border); padding-top: 8px; margin-top: 8px; }
.action-title { font-size: 12px; font-weight: 600; color: var(--text-bright); margin-bottom: 4px; }
.rows { margin-bottom: 4px; }
.row { display: flex; gap: 10px; font-size: 12px; padding: 3px 0; }
.k { color: var(--text-dim); min-width: 52px; flex-shrink: 0; }
.v { color: var(--text); word-break: break-all; }
.actions { display: flex; gap: 8px; margin-top: 10px; }
.btn { padding: 6px 16px; border-radius: 8px; border: none; font-size: 12px; font-weight: 600; cursor: pointer; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.yes { background: var(--orange); color: #fff; }
.no { background: #232736; color: var(--text-dim); }
.done-mark { margin-top: 8px; font-size: 12px; color: var(--green); }
</style>
