<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api/client'

const router = useRouter()
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  if (!password.value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    await login(password.value)
    router.push('/coach')
  } catch (e: any) {
    error.value = e.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="logo">FIT<span>·AI 体能教练</span></div>
      <p class="sub">请输入访问口令</p>
      <form @submit.prevent="submit">
        <input v-model="password" type="password" placeholder="访问口令" autofocus
          :disabled="loading" />
        <div v-if="error" class="err">{{ error }}</div>
        <button type="submit" :disabled="loading || !password">
          {{ loading ? '验证中...' : '进入' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%; display: flex; align-items: center; justify-content: center;
  background: radial-gradient(ellipse at 50% 30%, #1a1f2e 0%, var(--bg) 70%);
  padding: 20px;
}
.login-card {
  width: 100%; max-width: 360px; background: var(--card);
  border: 1px solid var(--border); border-radius: 16px; padding: 36px 30px;
}
.logo { font-size: 28px; font-weight: 800; color: var(--orange); }
.logo span { font-size: 13px; color: var(--text-dim); font-weight: 500; margin-left: 8px; }
.sub { color: var(--text-dim); font-size: 13px; margin: 8px 0 22px; }
input {
  width: 100%; padding: 11px 14px; border-radius: 10px; font-size: 14px;
  background: var(--bg-deep); border: 1px solid var(--border); color: var(--text-bright);
  outline: none; transition: border-color 0.15s;
}
input:focus { border-color: var(--orange); }
.err { color: var(--red); font-size: 12px; margin-top: 8px; }
button {
  width: 100%; margin-top: 16px; padding: 11px; border: none; border-radius: 10px;
  background: var(--orange); color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
}
button:disabled { opacity: 0.55; cursor: not-allowed; }
</style>
