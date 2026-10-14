<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authStore } from '../stores/auth'
import { ApiError } from '../api/client'

const route = useRoute()
const router = useRouter()

const mode = ref(route.query.mode === 'register' ? 'register' : 'login') // login | register
const email = ref('')
const password = ref('')
const nickname = ref('')
const submitting = ref(false)
const error = ref('')
const passwordError = ref('')
const isDev = typeof import.meta !== 'undefined' && !!import.meta.env?.DEV

const isLogin = computed(() => mode.value === 'login')

// 密码规则：8 字符且 UTF-8 不超过 72 字节（前端预检，后端为最终权威）
function validatePassword(pw) {
  if (pw.length < 8) return '密码至少 8 个字符'
  if (new TextEncoder().encode(pw).length > 72) return '密码过长：UTF-8 编码不得超过 72 字节'
  return ''
}

function switchMode(m) {
  if (submitting.value) return
  mode.value = m
  error.value = ''
  passwordError.value = ''
}

function onModeKey(event) {
  if (submitting.value || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const target = event.key === 'Home' ? 'login' : event.key === 'End' ? 'register' : mode.value === 'login' ? 'register' : 'login'
  switchMode(target)
  event.currentTarget.querySelector(`#auth-tab-${target}`)?.focus()
}

async function submit() {
  if (submitting.value) return
  error.value = ''
  passwordError.value = validatePassword(password.value)
  if (passwordError.value) return
  submitting.value = true
  try {
    if (mode.value === 'login') {
      await authStore.login(email.value.trim(), password.value)
    } else {
      await authStore.register(email.value.trim(), password.value, nickname.value.trim())
    }
    const redirect = route.query.redirect
    router.push(typeof redirect === 'string' && redirect.startsWith('/') ? redirect : { name: 'home' })
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '网络异常，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-wrap">
    <div class="auth-card">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true"></span>
        <div class="brand-text">
          <p class="eyebrow">CS2 档案 · 私有复盘</p>
          <h1 class="brand-name">比赛复盘</h1>
          <p class="brand-sub">上传录像，逐回合复盘，只保存在你的账户下</p>
        </div>
      </div>

      <div class="tabs" role="tablist" aria-label="登录或注册" @keydown="onModeKey">
        <button id="auth-tab-login" class="tab" :class="{ on: isLogin }" role="tab" aria-controls="auth-panel" :tabindex="isLogin ? 0 : -1" :aria-selected="isLogin" :disabled="submitting" @click="switchMode('login')">登录</button>
        <button id="auth-tab-register" class="tab" :class="{ on: !isLogin }" role="tab" aria-controls="auth-panel" :tabindex="isLogin ? -1 : 0" :aria-selected="!isLogin" :disabled="submitting" @click="switchMode('register')">注册</button>
      </div>

      <form id="auth-panel" role="tabpanel" :aria-labelledby="isLogin ? 'auth-tab-login' : 'auth-tab-register'" @submit.prevent="submit" class="form" novalidate>
        <template v-if="!isLogin">
          <div class="field">
            <label class="label" for="nickname">昵称</label>
            <input id="nickname" v-model="nickname" class="input" type="text" autocomplete="nickname" placeholder="怎么称呼你（1-64 字符）" maxlength="64" required />
          </div>
        </template>

        <div class="field">
          <label class="label" for="login-email">邮箱</label>
          <input id="login-email" v-model="email" class="input" type="email" autocomplete="email" placeholder="you@example.com" required />
        </div>

        <div class="field">
          <label class="label" for="login-password">密码</label>
          <input id="login-password" v-model="password" class="input" type="password" :autocomplete="isLogin ? 'current-password' : 'new-password'" :placeholder="isLogin ? '输入密码' : '至少 8 个字符，UTF-8 不超过 72 字节'" required />
          <p v-if="passwordError" class="field-error" role="alert">{{ passwordError }}</p>
        </div>

        <div v-if="error" class="form-error-box" role="alert">{{ error }}</div>

        <button type="submit" class="btn btn-primary submit" :disabled="submitting">
          {{ submitting ? '请稍候…' : isLogin ? '登 录' : '注册并进入' }}
        </button>

        <p v-if="!isLogin" class="hint">注册成功将直接登录，无需重复输入</p>
      </form>

      <!-- Steam 入口：诚实说明绑定发生在登录后（我的档案），不代办真实 Steam、不制造"已绑定" -->
      <div class="steam-entry">
        <div class="steam-head"><span class="steam-ic" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" width="15" height="15"><path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2Zm1.4 14.8-2.4-.4a2 2 0 0 0 .1-.7l2.4.4a4.3 4.3 0 1 0-1.1.7ZM9 16.3a1 1 0 1 1-1 1 1 0 0 1 1-1Zm3.1-1.3Z"/></svg></span><span class="steam-title">登录后绑定 Steam 身份</span></div>
        <p class="steam-desc">先使用邮箱登录，再到「我的档案」绑定 Steam，以识别比赛中的「你」。个人进阶功能还需满足对应套餐权限。</p>
      </div>

      <!-- 开发/测试模式诚实边界 -->
      <p v-if="isDev" class="dev-boundary">开发 / 测试环境：本环境可能使用测试身份，不会绑定你的真实 Steam 账号。</p>

      <div class="auth-note">
        <p class="note-head">关于你的档案</p>
        <p>上传的录像仅用于你本人的复盘分析，保存在你的账户下，不会对外公开。</p>
        <p>免费版每天可上传 1 场；进阶版解锁个人 / 团队复盘与 AI 教练。</p>
        <p class="note-sec">密码经加密存储，不保存明文；连续失败会触发限速保护。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-wrap {
  min-height: calc(100vh - var(--app-topbar-h));
  display: flex; align-items: center; justify-content: center;
  padding: 40px 16px;
  background:
    linear-gradient(rgba(91, 157, 217, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(91, 157, 217, 0.045) 1px, transparent 1px);
  background-color: var(--bg-base);
  background-size: 44px 44px;
}
.auth-card {
  width: 100%; max-width: 400px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 28px 24px 22px;
  box-shadow: none;
}
.brand { display: flex; align-items: flex-start; gap: 12px; }
.brand-mark { width: 10px; height: 10px; border-radius: 2px; background: var(--accent); transform: rotate(45deg); flex-shrink: 0; margin-top: 4px; }
.brand-text { min-width: 0; }
.brand-name { font-size: var(--f-title); font-weight: 800; letter-spacing: .3px; line-height: var(--lh-title); }
.brand-sub { color: var(--text-secondary); font-size: var(--f-sub); margin-top: 4px; line-height: 1.55; }

.tabs {
  display: grid; grid-template-columns: 1fr 1fr; gap: 4px;
  background: var(--bg-base); border-radius: var(--radius-sm); padding: 4px;
  margin-top: 18px;
}
.tab {
  border: none; background: transparent; color: var(--text-secondary);
  padding: 8px 0; border-radius: 4px; font-size: 14px; font-weight: 600; cursor: pointer; min-height: 40px;
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}
.tab.on { background: var(--bg-elevated); color: var(--accent); }

.form { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.label { font-size: var(--f-label); color: var(--text-secondary); font-weight: 600; margin-top: 8px; }
.submit { width: 100%; margin-top: 18px; padding: 11px; min-height: 46px; }
.hint { text-align: center; color: var(--text-muted); font-size: var(--f-label); margin-top: 12px; }

.steam-entry {
  margin-top: 18px; padding: 12px 14px;
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg-base);
}
.steam-head { display: flex; align-items: center; gap: 8px; }
.steam-ic { display: inline-flex; color: var(--info); }
.steam-title { font-size: var(--f-sub); font-weight: 700; color: var(--text-primary); }
.steam-desc { color: var(--text-muted); font-size: var(--f-label); line-height: 1.6; margin-top: 6px; }

.dev-boundary {
  margin-top: 12px; padding: 8px 12px;
  color: var(--warn); background: var(--warn-soft);
  border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent);
  border-radius: var(--radius-sm); font-size: 12px; line-height: 1.5;
}

.auth-note { border-top: 1px solid var(--border); margin-top: 18px; padding-top: 14px; display: flex; flex-direction: column; gap: 5px; }
.auth-note .note-head { color: var(--text-secondary); font-size: var(--f-label); font-weight: 600; }
.auth-note p { color: var(--text-muted); font-size: var(--f-label); line-height: 1.6; margin: 0; }
.auth-note .note-sec { color: var(--text-faint); }

@media (max-width: 720px) {
  .tab { min-height: 44px; }
}
</style>
