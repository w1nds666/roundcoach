/**
 * 6R21F-F2：Steam 绑定状态共享单例（App 壳层侧栏与 Dashboard 权益身份条同源读取）。
 * 登录后懒加载一次并缓存；失败静默降级为"未绑定/状态未知"（绝不伪造已绑定）。
 */
import { reactive } from 'vue'
import { api } from '../api/client'

const state = reactive({ status: null, loaded: false, error: '' })
let started = false

export const steamStore = {
  state,
  async load() {
    if (started) return state
    started = true
    try {
      state.status = await api.get('/api/auth/steam/status')
    } catch (e) {
      state.status = null
      state.error = e?.message || ''
    } finally {
      state.loaded = true
    }
    return state
  },
}
