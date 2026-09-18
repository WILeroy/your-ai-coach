import { defineStore } from 'pinia'
import type { ChatMessage, ViewSpec, PendingConfirm } from '../types'

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessionId: localStorage.getItem('fit_chat_session') || '',
    messages: [] as ChatMessage[],
    views: [] as ViewSpec[],
    activeViewIndex: -1,
    refreshToken: 0,
    sending: false,
    recoveringTurn: false,
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
    markPendingTurn(sessionId: string, message: string) {
      localStorage.setItem('fit_pending_turn', JSON.stringify({
        sessionId,
        message,
        startedAt: Date.now(),
      }))
    },
    clearPendingTurn() {
      localStorage.removeItem('fit_pending_turn')
      this.recoveringTurn = false
    },
    async recoverPendingTurn() {
      let pending: { sessionId?: string; message?: string; startedAt?: number }
      try {
        pending = JSON.parse(localStorage.getItem('fit_pending_turn') || 'null') || {}
      } catch {
        pending = {}
      }
      const sid = pending.sessionId
      const message = pending.message || ''
      if (!sid || !message) return
      // 超过2分钟的轮次视为终止，避免无限等待。
      if (!pending.startedAt || Date.now() - pending.startedAt > 120_000) {
        this.clearPendingTurn()
        return
      }

      this.recoveringTurn = true
      this.sessionId = sid
      localStorage.setItem('fit_chat_session', sid)
      this.messages = [
        { role: 'user', content: message },
        { role: 'assistant', content: '网络重连中：服务端正在完成上一轮，完成后自动显示结果…', streaming: true, toolTags: [] },
      ]
      let elapsed = 0
      const poll = window.setInterval(async () => {
        elapsed += 2000
        await this.loadHistory(sid)
        const userIdx = [...this.messages].reverse().findIndex(m => m.role === 'user' && m.content === message)
        const absoluteIdx = userIdx >= 0 ? this.messages.length - 1 - userIdx : -1
        if (absoluteIdx >= 0 && this.messages.length > absoluteIdx + 1) {
          window.clearInterval(poll)
          this.clearPendingTurn()
          this.loadSessions()
          return
        }
        if (elapsed >= 120_000) {
          window.clearInterval(poll)
          this.clearPendingTurn()
          this.messages = [
            { role: 'user', content: message },
            { role: 'assistant', content: '恢复超时：服务端没有保存上一轮结果，请重新发送。', toolTags: [] },
          ]
        }
      }, 2000)
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
          // 弱网/刷新时，写操作可能已生成pending但尚未写chat_messages。
          // 从SQLite恢复确认卡，避免用户永远无法点击确认。
          try {
            const pr = await fetch(`/api/chat/pending?session_id=${encodeURIComponent(id)}`)
            if (pr.ok) {
              const pending = await pr.json()
              if (pending?.action_id) {
                const confirm: PendingConfirm = {
                  action_id: pending.action_id,
                  tool: pending.actions?.[0]?.tool || '',
                  preview: pending.actions?.[0]?.preview || {},
                  actions: pending.actions || [],
                }
                let restored = [...(msgs.length ? msgs : this.messages)]
                const userText = pending.user_text || ''
                const userIdx = restored.map(m => m.content).lastIndexOf(userText)
                const alreadyPending = userIdx >= 0 &&
                  restored[userIdx + 1]?.confirm?.action_id === pending.action_id
                if (!alreadyPending) {
                  if (userText && (userIdx < 0 || restored.length === userIdx + 1)) {
                    if (userIdx < 0) restored.push({ role: 'user', content: userText })
                    restored.push({ role: 'assistant', content: '', confirm })
                  } else {
                    const last = restored[restored.length - 1]
                    if (last) last.confirm = confirm
                    else restored.push({ role: 'assistant', content: '', confirm })
                  }
                }
                this.messages = restored
                this.sessionId = id
                localStorage.setItem('fit_chat_session', id)
              }
            }
          } catch {}
        }
      } catch {}
    },
    newSession() {
      this.sessionId = ''
      this.messages = []
      this.views = []
      this.activeViewIndex = -1
      localStorage.removeItem('fit_chat_session')
    },
    async deleteSession(sid: string) {
      try {
        const r = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        this.sessions = this.sessions.filter(s => s.session_id !== sid)
        if (sid === this.sessionId) this.newSession()
      } catch (e) {
        console.error('delete session failed', e)
      }
    },
    async deleteAllSessions() {
      try {
        const r = await fetch('/api/chat/sessions', { method: 'DELETE' })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        this.sessions = []
        this.newSession()
      } catch (e) {
        console.error('delete all sessions failed', e)
      }
    },
    switchSession(sid: string) {
      this.sessionId = sid
      localStorage.setItem('fit_chat_session', sid)
      this.views = []
      this.activeViewIndex = -1
      this.loadHistory(sid)
    },
    pushView(spec: ViewSpec) {
      if (spec.view === 'refresh') return  // handled by pages via store
      if (spec.view === 'navigate') return // handled in send loop
      // Product form: newest result is active; recent results remain selectable history.
      this.views.unshift(spec)
      if (this.views.length > 12) this.views.pop()
      this.activeViewIndex = 0
    },
    selectView(i: number) {
      this.activeViewIndex = i
    },
    showBrief() {
      this.activeViewIndex = -1
    },
    refreshCanvas() {
      // Writes return to the refreshed daily brief so users see the persisted result.
      this.activeViewIndex = -1
      this.refreshToken++
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
      let finished = false
      // 客户端先生成session id：即使fetch在微信网络切换时还没收到首包，也能恢复本轮。
      const requestSessionId = this.sessionId || (
        Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
      )
      this.sessionId = requestSessionId
      localStorage.setItem('fit_chat_session', requestSessionId)
      this.markPendingTurn(requestSessionId, content)

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: content, session_id: requestSessionId }),
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
                this.markPendingTurn(this.sessionId, content)
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
                if (payload.view === 'refresh') this.refreshCanvas()
                else if (payload.view === 'navigate' && onNavigate) onNavigate(payload.page)
                else if (payload.view !== 'refresh') this.pushView(payload)
                break
              case 'pending_confirm': {
                const pc: PendingConfirm = {
                  action_id: payload.action_id, tool: payload.tool, preview: payload.preview || {},
                  actions: payload.actions || [],
                }
                // 新确认卡出现时，旧卡片置为已处理(避免点击过期卡片)
                this.messages = this.messages.map(m =>
                  m.confirm ? { ...m, confirm: { ...m.confirm, done: true } } : m)
                this.messages[idx] = { ...this.messages[idx], confirm: pc, streaming: false }
                break
              }
              case 'done':
                finished = true
                this.clearPendingTurn()
                streamText = payload.reply || streamText
                this.messages[idx] = { ...this.messages[idx], streaming: false }
                updateStream()
                break
              case 'error':
                finished = true
                this.clearPendingTurn()
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
          content: streamText || `网络中断: 服务端会继续完成本轮；重新进入页面后会自动恢复结果。 (${e.message || '连接已断开'})` }
      } finally {
        // 网络中断时保留pending标记，等待后台写入后由恢复轮询读回。
        if (finished) this.clearPendingTurn()
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
                if (payload.view === 'refresh') this.refreshCanvas()
                else if (payload.view !== 'navigate' && payload.view !== 'refresh') this.pushView(payload)
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
