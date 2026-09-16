<script setup lang="ts">
import { useChatStore } from '../stores/chat'
import type { PendingConfirm } from '../types'

const props = defineProps<{ confirm: PendingConfirm }>()
const store = useChatStore()

const TOOL_LABELS: Record<string, string> = {
  log_training: '📝 记录训练', update_session: '✏️ 修改训练', delete_data: '🗑️ 删除数据',
  adjust_plan: '📋 调整计划', create_plan: '🔄 生成周期', manage_exercises: '🏋️ 管理动作',
  log_body_metric: '📊 记录身体数据',
}

function rows(): [string, string][] {
  const out: [string, string][] = []
  const p = props.confirm.preview || {}
  for (const [k, v] of Object.entries(p)) {
    if (k === 'sets_summary' && Array.isArray(v)) {
      out.push(['动作', v.map((s: any) => {
        const g = (s.groups || []).join(' / ')
        return `${s.exercise}: ${g}${s.exists_in_db === false ? ' ⚠️动作库无此动作' : ''}`
      }).join('\n')])
    } else if (typeof v === 'object' && v !== null) {
      out.push([k, JSON.stringify(v)])
    } else if (v !== null && v !== undefined && v !== '') {
      out.push([k, String(v)])
    }
  }
  return out
}
</script>

<template>
  <div class="confirm-card">
    <div class="head">{{ TOOL_LABELS[confirm.tool] || confirm.tool }}</div>
    <div class="rows">
      <div v-for="([k, v], i) in rows()" :key="i" class="row">
        <span class="k">{{ k }}</span>
        <span class="v" style="white-space: pre-line">{{ v }}</span>
      </div>
    </div>
    <div class="actions">
      <button class="btn yes" :disabled="store.sending" @click="store.confirm(confirm.action_id, true)">确认执行</button>
      <button class="btn no" :disabled="store.sending" @click="store.confirm(confirm.action_id, false)">取消</button>
    </div>
  </div>
</template>

<style scoped>
.confirm-card {
  margin-top: 8px; background: #131620; border: 1px solid #3a3320;
  border-radius: 10px; padding: 12px;
}
.head { font-size: 13px; font-weight: 600; color: var(--orange); margin-bottom: 8px; }
.rows { margin-bottom: 10px; }
.row { display: flex; gap: 10px; font-size: 12px; padding: 3px 0; }
.k { color: var(--text-dim); min-width: 52px; flex-shrink: 0; }
.v { color: var(--text); word-break: break-all; }
.actions { display: flex; gap: 8px; }
.btn {
  padding: 6px 16px; border-radius: 8px; border: none; font-size: 12px;
  font-weight: 600; cursor: pointer;
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.yes { background: var(--orange); color: #fff; }
.no { background: #232736; color: var(--text-dim); }
</style>
