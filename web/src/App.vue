<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { logout } from './api/client'

const route = useRoute()
const router = useRouter()
const isLogin = computed(() => route.path === '/login')

const navs = [
  { path: '/coach', label: '教练', icon: '💬' },
  { path: '/dashboard', label: '仪表盘', icon: '📊' },
  { path: '/trends', label: '趋势', icon: '📈' },
  { path: '/review', label: '周报', icon: '📝' },
  { path: '/plan', label: '计划', icon: '📅' },
]
const mobileActive = computed(() => route.path)
</script>

<template>
  <div v-if="isLogin" class="login-wrap">
    <router-view />
  </div>
  <div v-else class="layout">
    <!-- 桌面侧栏 -->
    <aside class="sidebar">
      <div class="logo">
        <span class="logo-mark">FIT</span>
        <span class="logo-sub">AI 体能教练</span>
      </div>
      <nav class="side-nav">
        <router-link v-for="n in navs" :key="n.path" :to="n.path"
          class="side-item" :class="{ active: route.path === n.path }">
          <span class="icon">{{ n.icon }}</span>{{ n.label }}
        </router-link>
      </nav>
      <div class="side-footer">
        <button class="logout-btn" @click="logout()">退出</button>
      </div>
    </aside>

    <main class="main">
      <router-view />
    </main>

    <!-- 移动端底部导航 -->
    <nav class="bottom-nav">
      <router-link v-for="n in navs" :key="n.path" :to="n.path"
        class="bottom-item" :class="{ active: mobileActive === n.path }">
        <span class="icon">{{ n.icon }}</span>
        <span class="label">{{ n.label }}</span>
      </router-link>
    </nav>
  </div>
</template>

<style scoped>
.login-wrap { height: 100%; }
.layout { display: flex; height: 100vh; }
.sidebar {
  width: 190px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--bg-deep); border-right: 1px solid var(--border);
  padding: 20px 12px;
}
.logo { padding: 4px 10px 20px; }
.logo-mark { font-size: 22px; font-weight: 800; color: var(--orange); letter-spacing: 1px; }
.logo-sub { display: block; font-size: 11px; color: var(--text-dim); margin-top: 2px; }
.side-nav { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.side-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; border-radius: 10px;
  color: var(--text-dim); text-decoration: none; font-size: 14px; font-weight: 500;
  transition: all 0.15s;
}
.side-item:hover { color: var(--text-bright); background: var(--card); }
.side-item.active { color: var(--orange); background: var(--orange-soft); }
.side-item .icon { font-size: 16px; }
.side-footer { padding: 0 4px; }
.logout-btn {
  width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 10px;
  background: transparent; color: var(--text-dim); font-size: 12px; cursor: pointer;
}
.logout-btn:hover { color: var(--red); border-color: var(--red); }
.main { flex: 1; overflow-y: auto; padding-bottom: 64px; }

.bottom-nav { display: none; }
@media (max-width: 768px) {
  .sidebar { display: none; }
  .main { padding-bottom: 76px; }
  .bottom-nav {
    display: flex; position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
    background: var(--bg-deep); border-top: 1px solid var(--border);
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
  }
  .bottom-item {
    flex: 1; display: flex; flex-direction: column; align-items: center; gap: 1px;
    text-decoration: none; color: var(--text-dim); font-size: 10px; padding: 4px 0;
    border-radius: 8px;
  }
  .bottom-item.active { color: var(--orange); }
  .bottom-item .icon { font-size: 18px; }
}
</style>
