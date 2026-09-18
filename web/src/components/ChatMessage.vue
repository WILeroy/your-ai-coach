<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { ChatMessage } from '../types'
import ConfirmCard from './ConfirmCard.vue'

const props = defineProps<{ msg: ChatMessage }>()


const rendered = computed(() => {
  if (props.msg.role === 'user') return ''
  try {
    const html = marked.parse(props.msg.content || '', { async: false }) as string
    // 助手内容可能回显网页抓取/用户输入，marked 不转义原始HTML，必须消毒防XSS。
    return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } })
  } catch {
    return DOMPurify.sanitize(props.msg.content)
  }
})

const TOOL_LABELS: Record<string, string> = {
  get_today_context: '今日状态', get_exercise_history: '动作历史', get_sessions: '训练课',
  get_analytics: '分析', get_plan: '计划', get_body_metrics: '身体指标',
  search_exercises: '搜动作', show_view: '切页面', log_training: '记训练',
  update_session: '改训练', delete_data: '删数据', create_plan: '生成计划',
  adjust_plan: '调计划', log_body_metric: '记指标', manage_exercises: '动作库',
}
function label(t: string) { return TOOL_LABELS[t] || t }
</script>

<template>
  <div class="msg" :class="msg.role === 'user' ? 'msg-user' : 'msg-assistant'">
    <div class="avatar">{{ msg.role === 'user' ? '我' : 'FIT' }}</div>
    <div class="content">
      <div v-if="msg.role === 'user'" class="bubble user-bubble">{{ msg.content }}</div>
      <div v-else class="bubble assistant-bubble" :class="{ 'streaming-cursor': msg.streaming }">
        <div v-if="msg.toolTags?.length" class="tags">
          <span v-for="(t, i) in msg.toolTags" :key="i" class="tool-tag" :class="t.error ? 'err' : 'ok'">
            {{ t.error ? '✗' : '✓' }} {{ label(t.name) }}
          </span>
        </div>
        <div v-if="rendered" class="chat-bubble md" v-html="rendered"></div>
        <ConfirmCard v-if="msg.confirm" :confirm="msg.confirm" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg { display: flex; gap: 10px; margin-bottom: 16px; }
.msg-user { flex-direction: row-reverse; }
.avatar {
  width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
}
.msg-user .avatar { background: #232736; color: var(--text-dim); }
.msg-assistant .avatar { background: var(--orange-soft); color: var(--orange); }
.content { max-width: 86%; }
.bubble { border-radius: 12px; padding: 10px 14px; font-size: 13px; }
.user-bubble { background: #2a3245; color: var(--text-bright); border-top-right-radius: 4px; }
.assistant-bubble { background: var(--card); border: 1px solid var(--border); border-top-left-radius: 4px; }
.tags { margin-bottom: 8px; display: flex; flex-wrap: wrap; }
@media (max-width: 768px) { .content { max-width: 92%; } }
</style>
