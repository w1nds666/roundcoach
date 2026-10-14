import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { authStore } from './stores/auth'
import './styles/base.css'

// 启动：先校验登录态（/me），再挂载应用（路由守卫依赖登录态）
authStore.init().then(() => {
  createApp(App).use(router).mount('#app')
})
