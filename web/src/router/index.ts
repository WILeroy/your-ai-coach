import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/coach' },
    { path: '/login', component: () => import('../pages/LoginPage.vue') },
    { path: '/coach', component: () => import('../pages/CoachPage.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/coach' },
  ],
})

router.beforeEach((to) => {
  const authed = localStorage.getItem('fit_authed') === '1'
  if (to.path !== '/login' && !authed) return '/login'
})

export default router
