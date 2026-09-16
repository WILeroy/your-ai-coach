import { defineStore } from 'pinia'
import { api } from '../api/client'

export const useSummaryStore = defineStore('summary', {
  state: () => ({
    data: null as any,
    loading: false,
    loaded: false,
    refreshTick: 0,
  }),
  actions: {
    async load(force = false) {
      if (this.loading || (this.loaded && !force)) return
      this.loading = true
      try {
        this.data = await api.get('/api/summary')
        this.loaded = true
        this.refreshTick++
      } finally {
        this.loading = false
      }
    },
    refresh() { this.load(true) },
  },
})
