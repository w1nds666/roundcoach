<script setup>
/**
 * 6R14：「Steam 绑定」卡（Profile 内）。
 * - 未绑定：调用 /api/auth/steam/start?return_to=/profile 后跳转 Steam。
 * - 已绑定：展示掩码 id / provider / 最近验证 / 绑定时间；受控解绑（密码 → verify-password → unbind）。
 * - 永不输出完整 SteamID64；全部状态有显式提示，不静默失败。
 * - 父组件把 Steam 回调落地的 ?steam= 结果以 notice prop 传入展示。
 */
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '../api/client'

const props = defineProps({ notice: { type: Object, default: null } })

const status = ref(null)
const loading = ref(true)
const loadError = ref('')
const bindError = ref('')
const binding = ref(false)

const dialogOpen = ref(false)
const password = ref('')
const unbinding = ref(false)
const dialogError = ref('')

const bound = computed(() => !!status.value?.bound)

const STEAM_REASON_CN = {
  OPENID_STATE_INVALID: '登录状态已过期，请重新发起绑定',
  STEAM_IDENTITY_CONFLICT: '该 Steam 账号已绑定其他账号，无法重复绑定',
  STEAM_COOLDOWN: '当前处于 7 天换绑冷却期，暂不能绑定',
}

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function loadStatus() {
  loading.value = true
  loadError.value = ''
  try {
    status.value = await api.get('/api/auth/steam/status')
  } catch (e) {
    status.value = null
    loadError.value = e instanceof ApiError ? e.message : '绑定状态加载失败，请刷新重试'
  } finally {
    loading.value = false
  }
}

async function startBind() {
  if (binding.value) return
  binding.value = true
  bindError.value = ''
  try {
    const data = await api.get('/api/auth/steam/start?return_to=/profile')
    window.location = data.redirect_url
  } catch (e) {
    bindError.value = e instanceof ApiError ? e.message : '无法跳转到 Steam，请检查网络后重试'
  } finally {
    binding.value = false
  }
}

function openUnbind() {
  dialogOpen.value = true
  password.value = ''
  dialogError.value = ''
}
function closeUnbind() {
  if (unbinding.value) return
  dialogOpen.value = false
  password.value = ''
}

function unbindError(e) {
  if (e instanceof ApiError) {
    switch (e.code) {
      case 'INVALID_CREDENTIALS': return '密码错误，请重新输入'
      case 'STEAM_CSRF_INVALID': return '解绑凭证无效或已过期，请重新验证密码'
      case 'RECENT_AUTH_REQUIRED': return '需要最近 15 分钟内重新验证密码后才能解绑，请先验证'
      case 'STEAM_COOLDOWN': return '当前处于 7 天换绑冷却期，暂不能解绑'
      default: return e.message || '解绑失败，请稍后重试'
    }
  }
  return '网络异常，请检查连接后重试'
}

async function confirmUnbind() {
  if (unbinding.value) return
  if (!password.value.trim()) { dialogError.value = '请输入账号密码以确认解绑'; return }
  unbinding.value = true
  dialogError.value = ''
  try {
    const vp = await api.post('/api/auth/verify-password', { password: password.value })
    await api.post(`/api/auth/steam/unbind?recent_auth_token=${encodeURIComponent(vp.recent_auth_token)}`)
    dialogOpen.value = false
    password.value = ''
    await loadStatus()
  } catch (e) {
    dialogError.value = unbindError(e)
  } finally {
    unbinding.value = false
  }
}

onMounted(loadStatus)
</script>

<template>
  <div class="steam card">
    <div class="head">
      <h2 class="sub-h">Steam 绑定</h2>
      <span v-if="bound" class="pill pill-ok">已绑定</span>
      <span v-else-if="!loading && !loadError" class="pill pill-muted">未绑定</span>
    </div>

    <!-- Steam 回调结果（?steam=ok|cancel|error，父组件消费后传入） -->
    <p v-if="notice" class="notice" :class="`notice-${notice.tone}`">{{ notice.text }}</p>

    <div v-if="loading" class="state">加载绑定状态…</div>
    <p v-else-if="loadError" class="form-error-box">{{ loadError }}</p>

    <!-- 未绑定态 -->
    <template v-else-if="!bound">
      <p class="desc">
        绑定后，你的每场复盘会自动识别「这是你」，并按绑定的 Steam 身份解锁<strong>个人进阶分析</strong>。
        只保存经 Steam 官方验证的身份标识（SteamID64，界面仅显示掩码），用于匹配你参与的比赛；不会主动公开你的 Steam 资料。可随时解绑，解绑后需等待 7 天才能重新绑定/换绑。
      </p>
      <p v-if="bindError" class="form-error-box">{{ bindError }}</p>
      <button class="btn btn-primary" :disabled="binding" @click="startBind">
        {{ binding ? '正在跳转…' : '通过 Steam 登录并绑定' }}
      </button>
    </template>

    <!-- 已绑定态 -->
    <template v-else>
      <div class="meta">
        <div class="m-row"><span class="m-lbl">绑定的 Steam</span><span class="m-val num ellipsis">{{ status.steam_id64_masked }}</span></div>
        <div class="m-row"><span class="m-lbl">provider</span><span class="m-val">{{ status.provider }}</span></div>
        <div class="m-row" v-if="status.persona_name"><span class="m-lbl">Steam 昵称</span><span class="m-val"><span class="pill pill-info ellipsis">{{ status.persona_name }}</span></span></div>
        <div class="m-row"><span class="m-lbl">最近验证</span><span class="m-val num">{{ fmtTime(status.last_verified_at) }}</span></div>
        <div class="m-row"><span class="m-lbl">首次绑定</span><span class="m-val num">{{ fmtTime(status.bound_at) }}</span></div>
      </div>
      <p class="desc">
        Steam 昵称仅作为<strong>只读徽标</strong>展示，不会覆盖你在站内设置的昵称；本地昵称与 Steam 身份分层保存，解绑也不会丢失。
      </p>
      <p class="desc">
        解绑后你的个人进阶权益会<strong>暂停</strong>（历史数据保留），且需等待 <strong>7 天</strong>冷却期后才能重新绑定或换绑。
      </p>
      <button class="btn btn-ghost danger-btn" @click="openUnbind">解绑</button>
    </template>

    <details class="support">
      <summary>绑定/解绑遇到问题？</summary>
      <p>网络、冷却、凭证失效等情况无法自助解决时，可联系平台支持进一步处理。</p>
    </details>

    <!-- 解绑弹窗 -->
    <div v-if="dialogOpen" class="backdrop" @click.self="closeUnbind">
      <div class="modal card" role="dialog" aria-modal="true" aria-label="确认解绑 Steam">
        <h3>确认解绑 Steam</h3>
        <p class="modal-desc">出于安全，请再次输入你的账号密码。解绑后个人进阶权益暂停，且 7 天内不能重新绑定。</p>
        <label class="field">
          <span>账号密码</span>
          <input v-model="password" type="password" class="input" autocomplete="current-password" placeholder="输入账号密码" :disabled="unbinding" />
        </label>
        <p v-if="dialogError" class="form-error-box">{{ dialogError }}</p>
        <div class="actions">
          <button class="btn btn-primary" :disabled="unbinding" @click="confirmUnbind">{{ unbinding ? '解绑中…' : '确认解绑' }}</button>
          <button class="btn" :disabled="unbinding" @click="closeUnbind">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.steam { display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: center; gap: 10px; }
.sub-h { margin: 0; }
.state { color: var(--text-muted); font-size: 13px; padding: 6px 0; }
.desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; max-width: 76ch; }
.desc strong { color: var(--text-primary); font-weight: 600; }
.btn { align-self: flex-start; min-height: 44px; padding: 9px 18px; }
.danger-btn { justify-self: start; }
.meta { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg-elevated); }
.m-row { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.m-lbl { flex: 0 0 76px; color: var(--text-muted); font-size: 12.5px; }
.m-val { color: var(--text-primary); font-size: 13.5px; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }

.notice { padding: 9px 12px; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.6; }
.notice-ok { color: var(--ok); background: var(--ok-soft); border: 1px solid color-mix(in srgb, var(--ok) 30%, transparent); }
.notice-warn { color: var(--warn); background: var(--warn-soft); border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent); }
.notice-err { color: var(--err); background: var(--err-soft); border: 1px solid color-mix(in srgb, var(--err) 30%, transparent); }
.support { color: var(--text-muted); font-size: 12px; line-height: 1.6; border-top: 1px dashed var(--border); padding-top: 8px; }
.support summary { cursor: pointer; color: var(--text-secondary); font-weight: 600; }
.support p { margin-top: 6px; }

.backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, .55); display: grid; place-items: center; z-index: 20; padding: 16px; }
.modal { width: min(440px, 100%); display: flex; flex-direction: column; gap: 12px; }
.modal h3 { margin: 0; font-size: 18px; }
.modal-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.field { display: flex; flex-direction: column; gap: 5px; color: var(--text-secondary); font-size: 13px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.actions .btn { min-height: 44px; }
</style>
