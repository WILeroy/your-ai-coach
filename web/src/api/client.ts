import router from '../router'

async function request(path: string, options: RequestInit = {}): Promise<any> {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (resp.status === 401) {
    localStorage.removeItem('fit_authed')
    router.push('/login')
    throw new Error('unauthorized')
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.error || `HTTP ${resp.status}`)
  }
  return resp.json()
}

export const api = {
  get: (path: string) => request(path),
  post: (path: string, data?: unknown) =>
    request(path, { method: 'POST', body: JSON.stringify(data ?? {}) }),
}

export async function login(password: string) {
  const r = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.error || '登录失败')
  localStorage.setItem('fit_authed', '1')
  return body
}

export async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {})
  localStorage.removeItem('fit_authed')
  router.push('/login')
}

export async function checkAuth(): Promise<{ authed: boolean; password_set: boolean }> {
  const r = await fetch('/api/auth/check')
  return r.json()
}
