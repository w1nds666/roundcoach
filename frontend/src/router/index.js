import { createRouter, createWebHistory } from 'vue-router'
import { authStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('../views/HomeGate.vue') },
    { path: '/welcome', name: 'landing', component: () => import('../views/Landing.vue') },
    { path: '/upload', name: 'upload', component: () => import('../views/Upload.vue'), meta: { requiresAuth: true } },
    { path: '/profile', name: 'profile', component: () => import('../views/Profile.vue'), meta: { requiresAuth: true } },
    { path: '/coach', name: 'coach', component: () => import('../views/Coach.vue'), meta: { requiresAuth: true } },
    { path: '/demo/:id', name: 'report', component: () => import('../views/Report.vue'), meta: { requiresAuth: true } },
    { path: '/share', name: 'share', component: () => import('../views/Share.vue') },
    { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { guestOnly: true } },
    // 6R21F dev-only 内部实验室：数据来自仅 dev 的路由（生产 build 无该路由 → 实验室 fail-closed，不暴露数据）
    { path: '/map-control-lab', name: 'mapControlLab', component: () => import('../views/MapControlLab.vue') },
  ],
})

// 路由守卫：未登录访问受保护页 → 登录（带回跳地址）；已登录访问登录页 → 首页
router.beforeEach((to) => {
  const authed = !!authStore.state.user
  if (to.meta.requiresAuth && !authed) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && authed) {
    return { name: 'home' }
  }
  return true
})

export default router
