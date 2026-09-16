import { defineStore } from 'pinia'
import type { ChatMessage, ViewSpec, PendingConfirm } from '../types'

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessionId: localStorage.getItem('fit_chat_session') || '',
    messages: [] as ChatMessage[],
    views: [] as ViewSpec[],
    sending: false,
    status: { configured: false, model: '', base_url: '' },
    sessions: [] as { session_id: string; title: string; last_at: string; msg_count: number }[],
  }),
  actions: {
    async loadStatus() {
      try {
        const r = await fetch('/api/chat/status')
        if (r.ok) this.status = await r.json()
      } catch {}
    },
    async loadSessions() {
      try {
        const r = await fetch('/api/chat/sessions')
        if (r.ok) this.sessions = await r.json()
      } catch {}
    },
    async loadHistory(sid?: string) {
      const id = sid || this.sessionId
      if (!id) { this.messages = []; return }
      try {
        const r = await fetch(`/api/chat/history?session_id=${encodeURIComponent(id)}`)
        if (r.ok) {
          const msgs: ChatMessage[] = await r.json()
          if (msgs.length) {
            this.messages = msgs
            this.sessionId = id
            localStorage.setItem('fit_chat_session', id)
          }
        }
      } catch {}
    },
    newSession() {
      this.sessionId = ''
      this.messages = []
      this.views = []
      localStorage.removeItem('fit_chat_session')
    },
    switchSession(sid: string) {
      this.sessionId = sid
      localStorage.setItem('fit_chat_session', sid)
      this.views = []
      this.loadHistory(sid)
    },
    pushView(spec: ViewSpec) {
      if (spec.view === 'refresh') return  // handled by pages via store
      if (spec.view === 'navigate') return // handled in send loop
      // keep last 4 views, newest first
      this.views.unshift(spec)
      if (this.views.length > 4) this.views.pop()
    },
    async send(text: string, onNavigate?: (page: string) => void) {
      const content = text.trim()
      if (!content || this.sending) return
      this.sending = true
      this.messages.push({ role: 'user', content })

      const streamMsg: ChatMessage = { role: 'assistant', content: '', toolTags: [], streaming: true }
      const idx = this.messages.push(streamMsg) - 1
      let streamText = ''
      let tags: { name: string; error?: boolean }[] = []

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: content, session_id: this.sessionId || undefined }),
        })
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)

        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let partial = ''
        const updateStream = () => {
          // 按索引替换对象，保证 Vue 响应式触发重渲染
          this.messages[idx] = { ...this.messages[idx], content: streamText, toolTags: [...tags] }
        }

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          partial += decoder.decode(value, { stream: true })
          const parts = partial.split('\n\n')
          partial = parts.pop() || ''
          for (const part of parts) {
            if (!part.trim()) continue
            let eventType = 'message'
            let data = ''
            for (const line of part.split('\n')) {
              if (line.startsWith('event: ')) eventType = line.slice(7).trim()
              else if (line.startsWith('data: ')) data = line.slice(6)
            }
            if (!data) continue
            let payload: any
            try { payload = JSON.parse(data) } catch { continue }

            switch (eventType) {
              case 'session':
                this.sessionId = payload.session_id
                localStorage.setItem('fit_chat_session', this.sessionId)
                break
              case 'delta':
                streamText += payload.text || ''
                updateStream()
                break
              case 'tool_call':
                tags.push({ name: payload.tool })
                updateStream()
                break
              case 'tool_result':
                if (tags.length) tags[tags.length - 1].error = (payload.result || '').includes('"error"')
                updateStream()
                break
              case 'ui':
                if (payload.view === 'navigate' && onNavigate) onNavigate(payload.page)
                else if (payload.view !== 'refresh') this.pushView(payload)
                break
              case 'pending_confirm': {
                const pc: PendingConfirm = {
                  action_id: payload.action_id, tool: payload.tool, preview: payload.preview || {},
                  actions: payload.actions || [],
                }
                this.messages[idx] = { ...this.messages[idx], confirm: pc, streaming: false }
                break
              }
              case 'done':
                streamText = payload.reply || streamText
                this.messages[idx] = { ...this.messages[idx], streaming: false }
                updateStream()
                break
              case 'error':
                streamText += `\n\n❌ ${payload.message || '出错了'}`
                this.messages[idx] = { ...this.messages[idx], streaming: false }
                updateStream()
                break
            }
          }
        }
        this.messages[idx] = { ...this.messages[idx], streaming: false,
          content: streamText || this.messages[idx].content || (this.messages[idx].confirm ? '' : '(空回复，请重试)') }
      } catch (e: any) {
        this.messages[idx] = { ...this.messages[idx], streaming: false,
          content: streamText || `网络出错: ${e.message || '请重试'}` }
      } finally {
        this.sending = false
        this.loadSessions()
      }
    },
    async confirm(actionId: string, confirmed: boolean) {
      const pc = [...this.messages].reverse().find((m: ChatMessage) => m.confirm?.action_id === actionId)?.confirm
      if (!pc) return
      this.sending = true
      const streamMsg: ChatMessage = { role: 'assistant', content: '', toolTags: [], streaming: true }
      const idx = this.messages.push(streamMsg) - 1
      let streamText = ''
      let tags: { name: string; error?: boolean }[] = []
      try {
        const resp = await fetch('/api/chat/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action_id: actionId, confirmed, session_id: this.sessionId }),
        })
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let partial = ''
        const updateStream = () => {
          this.messages[idx] = { ...this.messages[idx], content: streamText, toolTags: [...tags] }
        }
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          partial += decoder.decode(value, { stream: true })
          const parts = partial.split('\n\n')
          partial = parts.pop() || ''
          for (const part of parts) {
            if (!part.trim()) continue
            let eventType = 'message'
            let data = ''
            for (const line of part.split('\n')) {
              if (line.startsWith('event: ')) eventType = line.slice(7).trim()
              else if (line.startsWith('data: ')) data = line.slice(6)
            }
            if (!data) continue
            let payload: any
            try { payload = JSON.parse(data) } catch { continue }
            switch (eventType) {
              case 'delta': streamText += payload.text || ''; updateStream(); break
              case 'tool_call': tags.push({ name: payload.tool }); updateStream(); break
              case 'tool_result':
                if (tags.length) tags[tags.length - 1].error = (payload.result || '').includes('"error"')
                updateStream()
                break
              case 'ui':
                if (payload.view !== 'navigate' && payload.view !== 'refresh') this.pushView(payload)
                break
              case 'done':
                streamText = payload.reply || streamText
                this.messages[idx] = { ...this.messages[idx], streaming: false }
                updateStream()
                break
              case 'error':
                streamText += `\n\n❌ ${payload.message || '出错了'}`
                this.messages[idx] = { ...this.messages[idx], streaming: false }
                updateStream()
                break
            }
          }
        }
        this.messages[idx] = { ...this.messages[idx], streaming: false,
          content: streamText || '(完成)' }
        pc.preview = {} // disable old card buttons
        ;(pc as any).done = true
      } catch (e: any) {
        this.messages[idx] = { ...this.messages[idx], streaming: false,
          content: `确认出错: ${e.message || '请重试'}` }
      } finally {
        this.sending = false
      }
    },
  },
})
