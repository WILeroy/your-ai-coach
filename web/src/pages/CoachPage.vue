<script setup lang="ts">
import { ref, onMounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import ChatMessage from '../components/ChatMessage.vue'
import DynamicCanvas from '../components/DynamicCanvas.vue'

const store = useChatStore()
const router = useRouter()
const input = ref('')
const listEl = ref<HTMLElement>()
const showSessions = ref(false)

const QUICK = ['今天练什么？', '深蹲趋势如何？', '本周训练情况', '我的负荷状态怎么样？']

onMounted(() => {
  store.loadStatus()
  store.loadSessions()
  store.loadHistory()
})

watch(() => store.messages.length, async () => {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}, { deep: true })

function send(text?: string) {
  const content = (text || input.value).trim()
  if (!content) return
  input.value = ''
  store.send(content, (page) => {
    if (['dashboard', 'trends', 'review', 'plan'].includes(page)) router.push('/' + page)
  })
}
</script>

<template>
  <div class="coach">
    <div class="chat-pane">
      <div class="chat-head">
        <div class="title">
          AI 教练
          <span v-if="store.status.model" class="model">{{ store.status.model }}</span>
        </div>
        <div class="head-actions">
          <button class="hbtn" @click="showSessions = !showSessions">历史</button>
          <button class="hbtn" @click="store.newSession()">新会话</button>
        </div>
      </div>

      <!-- 历史会话抽屉 -->
      <div v-if="showSessions" class="sessions-drawer">
        <div v-if="!store.sessions.length" class="no-sessions">暂无历史会话</div>
        <div v-for="s in store.sessions" :key="s.session_id" class="session-item"
          :class="{ active: s.session_id === store.sessionId }"
          @click="store.switchSession(s.session_id); showSessions = false">
          <div class="s-title">{{ s.title || s.session_id }}</div>
          <div class="s-meta">{{ (s.last_at || '').slice(0, 16).replace('T', ' ') }} · {{ s.msg_count }}条</div>
        </div>
      </div>

      <div ref="listEl" class="chat-list">
        <div v-if="!store.messages.length" class="welcome">
          <div class="w-icon">🏋️</div>
          <p class="w-title">你好，我是 FIT</p>
          <p class="w-sub">可以帮你记录训练、分析进展、调整计划。数据查询会自动在右侧画布出图。</p>
          <div class="quick">
            <button v-for="q in QUICK" :key="q" @click="send(q)">{{ q }}</button>
          </div>
        </div>
        <ChatMessage v-for="(m, i) in store.messages" :key="i" :msg="m" />
      </div>

      <div class="input-row">
        <textarea v-model="input" rows="2" placeholder="输入训练数据或问题，Enter 发送 / Shift+Enter 换行"
          :disabled="store.sending"
          @keydown.enter.exact.prevent="send()" />
        <button class="send-btn" :disabled="store.sending || !input.trim()" @click="send()">
          {{ store.sending ? '…' : '发送' }}
        </button>
      </div>
    </div>

    <div class="canvas-pane">
      <DynamicCanvas />
    </div>
  </div>
</template>

<style scoped>
.coach { display: flex; height: 100vh; }
.chat-pane {
  flex: 1.4; min-width: 0; display: flex; flex-direction: column;
  border-right: 1px solid var(--border);
}
.canvas-pane { flex: 1; min-width: 0; padding: 14px; }

.chat-head {
  display: flex; align-items: center; padding: 14px 18px;
  border-bottom: 1px solid var(--border); background: var(--bg-deep);
}
.title { font-size: 15px; font-weight: 700; color: var(--text-bright); }
.model {
  font-size: 10px; font-weight: 500; color: var(--text-dim);
  background: #232736; padding: 2px 8px; border-radius: 10px; margin-left: 8px;
}
.head-actions { margin-left: auto; display: flex; gap: 8px; }
.hbtn {
  background: none; border: 1px solid var(--border); color: var(--text-dim);
  font-size: 12px; padding: 5px 12px; border-radius: 8px; cursor: pointer;
}
.hbtn:hover { color: var(--orange); border-color: var(--orange); }

.sessions-drawer {
  max-height: 240px; overflow-y: auto; background: var(--bg-deep);
  border-bottom: 1px solid var(--border); padding: 8px 12px;
}
.no-sessions { color: var(--text-dim); font-size: 12px; padding: 12px; text-align: center; }
.session-item { padding: 8px 10px; border-radius: 8px; cursor: pointer; }
.session-item:hover { background: var(--card); }
.session-item.active { background: var(--orange-soft); }
.s-title { font-size: 13px; color: var(--text-bright); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.s-meta { font-size: 10px; color: var(--text-dim); }

.chat-list { flex: 1; overflow-y: auto; padding: 18px; }
.welcome {
  height: 100%; display: flex; flex-direction: column; align-items: center;
  justify-content: center; color: var(--text-dim); text-align: center; padding: 20px;
}
.w-icon { font-size: 40px; margin-bottom: 10px; }
.w-title { font-size: 17px; font-weight: 700; color: var(--text-bright); }
.w-sub { font-size: 12px; margin: 8px 0 18px; max-width: 380px; }
.quick { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.quick button {
  background: var(--card); border: 1px solid var(--border); color: var(--text);
  font-size: 12px; padding: 7px 14px; border-radius: 18px; cursor: pointer; transition: 0.15s;
}
.quick button:hover { border-color: var(--orange); color: var(--orange); }

.input-row {
  display: flex; gap: 10px; padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--border); background: var(--bg-deep);
}
textarea {
  flex: 1; resize: none; background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; color: var(--text-bright); padding: 10px 14px;
  font-size: 13px; font-family: inherit; outline: none; line-height: 1.5;
}
textarea:focus { border-color: var(--orange); }
.send-btn {
  align-self: flex-end; padding: 10px 22px; background: var(--orange); color: #fff;
  border: none; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer;
}
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }

@media (max-width: 768px) {
  .coach { flex-direction: column; height: auto; min-height: 100vh; }
  .chat-pane { border-right: none; height: calc(100vh - 64px); }
  .canvas-pane {
    display: none; height: auto; min-height: 60vh; padding: 0;
  }
  .canvas-pane:target, .coach.show-canvas .canvas-pane { display: block; }
}
</style>
