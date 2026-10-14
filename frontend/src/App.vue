<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authStore } from './stores/auth'
import { steamStore } from './stores/steam'

const router = useRouter()
const route = useRoute()
const user = computed(() => authStore.state.user)
const authed = computed(() => !!user.value)
const marketingHome = computed(() => route.name === 'landing' || (route.name === 'home' && !authed.value))
const standalonePublic = computed(() => route.name === 'share')
const loggedIn = computed(() => authed.value && !marketingHome.value)

// ---- 档位（有效档位：过期 pro/max 降级 free）----
const TIER_LABEL = { free: '免费版', pro: '进阶版', max: '团队版' }
const effectiveTier = computed(() => {
  const u = user.value
  if (!u || u.subscription_tier === 'free') return 'free'
  if (u.subscription_expire && new Date(u.subscription_expire).getTime() < Date.now()) return 'free'
  return u.subscription_tier
})
const tierLabel = computed(() => TIER_LABEL[effectiveTier.value] || '免费版')
const expired = computed(() => !!user.value && user.value.subscription_tier !== 'free' && effectiveTier.value === 'free')

// ---- Steam 绑定状态（共享单例；登录后懒加载）----
onMounted(() => { if (authed.value) steamStore.load() })
const steamBound = computed(() => !!steamStore.state.status?.bound)
const steamMasked = computed(() => steamStore.state.status?.steam_id64_masked || '')

// ---- 面包屑（由路由推导，不做机器味）----
const CRUMB = {
  home: { label: '我的复盘' },
  landing: { label: '欢迎' },
  upload: { label: '上传' },
  coach: { label: '长期教练' },
  profile: { label: '我的档案' },
  share: { label: '分享' },
  report: { label: '报告' },
  login: { label: '登录' },
}
const crumb = computed(() => {
  const m = CRUMB[route.name]
  if (route.name === 'home') return { root: '', cur: m?.label || '我的复盘' }
  return { root: '我的复盘', cur: m?.label || '' }
})

const NAV = [
  { label: '产品首页', to: '/welcome', icon: 'home' },
  { label: '我的复盘', to: '/', icon: 'dash', exact: true },
  { label: '上传', to: '/upload', icon: 'upload' },
  { label: '长期教练', to: '/coach', icon: 'coach' },
  { label: '我的档案', to: '/profile', icon: 'profile' },
]

// 简洁线性图标（1.6px 描边，克制、非 emoji）
const ICONS = {
  home: 'M3 11.5 12 4l9 7.5M5.5 10v10h13V10M9 20v-6h6v6',
  dash: 'M4 13h6V4H4v9Zm10 7h6v-9h-6v9ZM4 20h6v-4H4v4Zm10-13h6V4h-6v3Z',
  upload: 'M12 16V4m0 0 4 4m-4-4L8 8M4 20h16',
  coach: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z',
  profile: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-8 8a8 8 0 0 1 16 0',
}

function logout() {
  authStore.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <!-- 营销页与匿名分享页不包账户壳层，避免公开报告暴露浏览者的站内身份状态 -->
  <div v-if="marketingHome || standalonePublic">
    <router-view />
  </div>

  <!-- 登录页（游客）：壳层只保留轻量顶栏，不给侧栏 -->
  <div v-else-if="!loggedIn" class="app-shell">
    <header class="app-topbar">
      <router-link to="/welcome" class="app-brand"><span class="brand-dot"></span>比赛复盘</router-link>
      <div class="spacer"></div>
      <router-link to="/login" class="btn btn-primary">登录 / 注册</router-link>
    </header>
    <main class="content"><router-view /></main>
    <footer class="app-foot">数据来自你上传的比赛录像，仅用于复盘分析</footer>
  </div>

  <!-- 已登录应用壳层：顶栏 + 侧栏 + 内容 -->
  <div v-else class="app-shell">
    <header class="app-topbar">
      <router-link to="/welcome" class="app-brand"><span class="brand-dot"></span>比赛复盘</router-link>
      <nav class="app-crumb" :aria-label="`面包屑：${crumb.cur}`">
        <template v-if="crumb.root">
          <router-link to="/" class="crumb-root">{{ crumb.root }}</router-link>
          <span class="sep">/</span>
        </template>
        <span class="cur ellipsis">{{ crumb.cur }}</span>
      </nav>
      <div class="spacer"></div>
      <div class="app-user user">
        <router-link to="/profile" class="app-user-link">
          <span class="app-avatar">{{ (user.nickname || '?').slice(0, 1).toUpperCase() }}</span>
          <span class="app-uname ellipsis">{{ user.nickname }}</span>
        </router-link>
        <span class="tier-badge" :class="`tier-${effectiveTier}`" :title="expired ? '订阅已过期，已降级为免费版' : ''">{{ expired ? '免费版（已过期）' : tierLabel }}</span>
        <button class="btn btn-ghost logout" @click="logout">退出</button>
      </div>
    </header>

    <div class="app-body">
      <!-- 移动端导航条（flow 定位，不遮挡正文） -->
      <nav class="app-nav-strip" :aria-label="`主导航：${crumb.cur}`">
        <router-link v-for="n in NAV" :key="n.label" :to="n.to" class="app-nav-link" :class="{ active: (n.exact ? route.path === n.to : route.path.startsWith(n.to)) }">
          {{ n.label }}
        </router-link>
      </nav>

      <aside class="app-side" :aria-label="`主导航：${crumb.cur}`">
        <p class="app-nav-label">工作台</p>
        <nav class="app-nav">
          <router-link v-for="n in NAV" :key="n.label" :to="n.to" class="app-nav-link" :class="{ active: (n.exact ? route.path === n.to : route.path.startsWith(n.to)) }">
            <span class="app-nav-ic" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path :d="ICONS[n.icon]" /></svg></span>
            {{ n.label }}
          </router-link>
        </nav>
        <div class="app-side-foot">
          <div class="identity-box" aria-label="账户权益与身份">
            <div class="identity-row"><span class="lb">档位</span><span class="val"><span class="tier-badge" :class="`tier-${effectiveTier}`">{{ expired ? '免费版（已过期）' : tierLabel }}</span></span></div>
            <div class="identity-row">
              <span class="lb">Steam</span>
              <span class="val identity-pill" :class="(steamStore.state.loaded ? (steamBound ? 'idp-bound' : 'idp-unbound') : 'idp-unbound')">
                <span class="dot" aria-hidden="true"></span>{{ steamStore.state.loaded ? (steamBound ? '已绑定' : '未绑定') : '…' }}
              </span>
            </div>
            <p class="identity-hint" v-if="effectiveTier !== 'free' && steamStore.state.loaded && !steamBound">绑定 Steam 后解锁个人进阶分析</p>
            <p class="identity-hint" v-else-if="effectiveTier === 'free'">免费版：每天可上传 1 场</p>
          </div>
        </div>
      </aside>

      <main class="content app-content">
        <router-view />
      </main>
    </div>
    <footer class="app-foot">数据来自你上传的比赛录像，仅用于复盘分析</footer>
  </div>
</template>

<style scoped>
.app-shell .content { min-height: 0; }
.app-user-link { display: inline-flex; align-items: center; gap: 8px; color: var(--text-primary); }
.app-nav-strip .app-nav-link { color: var(--text-secondary); }
</style>
