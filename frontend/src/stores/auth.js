/**
 * 轻量 auth store（Vue reactive 单例，不引入重型状态库）
 * - 登录态持久化（token 存 localStorage，user 信息缓存）
 * - 启动时用 /me 校验 token 有效性
 * - 401 事件自动登出
 */
import { reactive } from 'vue'
import { api, authEvents, tokenStore } from '../api/client'

const state = reactive({
  user: null,       // {id, email, nickname, subscription_tier, subscription_expire, ...}
  initialized: false,
})

const USER_KEY = 'cs2_user'

function persist(user) {
  state.user = user
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
  else localStorage.removeItem(USER_KEY)
}

function applyAuth(payload) {
  tokenStore.set(payload.access_token)
  persist(payload.user)
  return payload.user
}

export const authStore = {
  state,

  /** 应用启动：有 token 则 /me 校验；失败自动登出。 */
  async init() {
    const cached = localStorage.getItem(USER_KEY)
    if (cached) {
      try { state.user = JSON.parse(cached) } catch { state.user = null }
    }
    if (tokenStore.get()) {
      try {
        const me = await api.get('/api/auth/me')
        persist(me)
      } catch {
        // 401 已在 client 层清理；其他错误（网络）保留缓存但不阻塞
      }
    }
    state.initialized = true
  },

  async login(email, password) {
    const payload = await api.post('/api/auth/login', { email, password })
    return applyAuth(payload)
  },

  /** 注册成功直接使用返回的 token 进入应用（计划书契约） */
  async register(email, password, nickname) {
    const payload = await api.post('/api/auth/register', { email, password, nickname })
    return applyAuth(payload)
  },

  logout() {
    tokenStore.clear()
    persist(null)
  },
}

// 401 广播 → 自动登出（路由守卫负责跳转）
authEvents.addEventListener('auth:expired', () => {
  tokenStore.clear()
  persist(null)
})
