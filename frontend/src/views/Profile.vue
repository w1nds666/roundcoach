<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authStore } from '../stores/auth'
import { api } from '../api/client'
import { tierInfo, isPro, isMax } from '../utils/subscription'
import { mapLabel } from '../utils/mapNames'
import SteamBind from '../components/SteamBind.vue'
import TeamWorkspace from '../components/TeamWorkspace.vue'

const router = useRouter()
const route = useRoute()
const user = computed(() => authStore.state.user)
const demos = ref([])
const loading = ref(true)
const recordsError = ref('')
const tier = computed(() => tierInfo(user.value))
const coachPill = computed(() => {
  if (isMax(user.value)) return { label: '已解锁 · 30 场', cls: 'pill-ok' }
  if (isPro(user.value)) return { label: '已解锁 · 10 场', cls: 'pill-ok' }
  return { label: '进阶版', cls: 'pill-muted' }
})
const steamNotice = ref(null)

// Steam 回调落地的 ?steam=ok|cancel|error 结果（消费一次即清理）
const STEAM_REASON_CN = {
  OPENID_STATE_INVALID: '登录状态已过期，请重新发起绑定',
  STEAM_IDENTITY_CONFLICT: '该 Steam 账号已绑定其他账号，无法重复绑定',
  STEAM_COOLDOWN: '当前处于 7 天换绑冷却期，暂不能绑定',
}

const demoStats = computed(() => ({
  total: demos.value.length,
  parsed: demos.value.filter((d) => d.status === 'parsed').length,
  parsing: demos.value.filter((d) => d.status === 'parsing' || d.status === 'uploaded').length,
  failed: demos.value.filter((d) => d.status === 'failed').length,
}))

const STATUS = {
  uploaded: { cls: 'pill-muted', label: '已上传' },
  parsing:  { cls: 'pill-info',  label: '解析中' },
  parsed:   { cls: 'pill-ok',    label: '已解析' },
  failed:   { cls: 'pill-err',   label: '失败' },
}

onMounted(async () => {
  // Steam 回调消费一次并清理：有 ?steam=... 则提示用户，清除 query，刷新 /me 与绑定状态。
  const steam = route.query.steam
  if (steam) {
    const reason = route.query.reason
    if (steam === 'ok') steamNotice.value = { tone: 'ok', text: 'Steam 账号绑定成功，已自动识别你的身份。' }
    else if (steam === 'cancel') steamNotice.value = { tone: 'warn', text: '你已取消 Steam 绑定。' }
    else if (steam === 'error') steamNotice.value = { tone: 'err', text: `Steam 绑定失败：${STEAM_REASON_CN[reason] || reason || '请稍后重试'}` }
    router.replace({ query: { ...route.query, steam: undefined, reason: undefined } })
    try { await authStore.init() } catch { /* /me 刷新失败不阻塞个人中心 */ }
  }
  await loadRecords()
})

async function loadRecords() {
  if (recordsRequestPending) return
  recordsRequestPending = true
  loading.value = true
  recordsError.value = ''
  try {
    demos.value = await api.get('/api/demos?limit=100')
  } catch {
    recordsError.value = '比赛记录暂时无法加载，请重试。'
  } finally {
    loading.value = false
    recordsRequestPending = false
  }
}
let recordsRequestPending = false

function logout() {
  authStore.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="page-wrap profile">
    <div class="page-head">
      <div class="profile-lead">
        <p class="eyebrow">个人中心 / 账号</p>
        <h1 class="page-title">我的档案</h1>
        <p class="page-sub">身份、权益、团队与比赛记录集中在一处；每一项授权都能看见边界。</p>
      </div>
      <span class="pill" :class="tier.cls">{{ tier.label }}</span>
    </div>

    <section class="identity-command" aria-label="账号身份与权益概览">
      <div class="acct">
        <div class="avatar">{{ (user?.nickname || '?').slice(0, 1).toUpperCase() }}</div>
        <div class="acct-info">
          <div class="identity-kicker">站内身份</div>
          <div class="name ellipsis">{{ user?.nickname }}</div>
          <div class="email num">{{ user?.email }}</div>
        </div>
      </div>

      <div class="sub">
        <span class="identity-kicker">当前权益</span>
        <div class="tier-line"><strong>{{ tier.label }}</strong><span class="pill pill-ok">有效</span></div>
        <p class="desc">{{ tier.desc }}</p>
        <div class="upgrade">
          <span class="upgrade-note">进阶版（月卡 39 元 / 年卡 299 元） · 支付尚未开放</span>
        </div>
      </div>

      <div class="stats">
        <div class="stat"><span class="num">{{ loading || recordsError ? '—' : demoStats.total }}</span><span class="lbl">已加载</span></div>
        <div class="stat ok"><span class="num">{{ loading || recordsError ? '—' : demoStats.parsed }}</span><span class="lbl">已解析</span></div>
        <div class="stat info"><span class="num">{{ loading || recordsError ? '—' : demoStats.parsing }}</span><span class="lbl">处理中</span></div>
        <div class="stat err"><span class="num">{{ loading || recordsError ? '—' : demoStats.failed }}</span><span class="lbl">失败</span></div>
      </div>
    </section>
    <p class="page-sub records-scope">统计范围：最近最多 100 场未删除的比赛；不是账户历史总量。<router-link to="/">查看全部记录 →</router-link></p>

    <div class="profile-workspace">
      <div class="profile-main">
        <div class="workspace-head">
          <span class="workspace-index">01</span>
          <div><h2>身份与训练</h2><p>Steam 只负责比赛身份；站内昵称、训练画像与历史记录分层保存。</p></div>
        </div>
        <SteamBind :notice="steamNotice" />
        <router-link to="/coach" class="card coach-entry">
          <div class="coach-head">
            <h2 class="sub-h">长期教练</h2>
            <span class="pill" :class="coachPill.cls">{{ coachPill.label }}</span>
          </div>
          <p class="coach-desc">
            以绑定的 Steam 身份持续累计你的长期画像：重复问题、趋势变化、最值得改的 3 件事，并可点击证据回到对应回合。
          </p>
          <span class="coach-go">进入长期教练 →</span>
        </router-link>

        <section class="section match-section">
          <div class="section-head">
            <h2 class="sec">比赛记录</h2>
            <span v-if="!loading && !recordsError" class="sh-note muted">最近 {{ Math.min(20, demos.length) }} 场</span>
          </div>
          <div v-if="loading" class="card">加载中…</div>
          <div v-else-if="recordsError" class="card" role="alert">
            <p>{{ recordsError }}</p>
            <button class="btn btn-secondary" @click="loadRecords">重试加载比赛记录</button>
          </div>
          <div v-else-if="demos.length === 0" class="card empty">暂无未删除的比赛记录</div>
          <div v-else class="list">
            <div v-for="d in demos.slice(0, 20)" :key="d.id" class="card row">
              <router-link :to="`/demo/${d.id}`" class="fname">{{ mapLabel(d.map_name) || d.file_name }}</router-link>
              <span v-if="d.map_name" class="map map-tech">{{ d.map_name }}</span>
              <span class="pill" :class="STATUS[d.status]?.cls || 'pill-muted'">{{ STATUS[d.status]?.label || d.status }}</span>
            </div>
          </div>
        </section>
      </div>

      <aside class="profile-side" aria-label="团队与隐私">
        <div class="workspace-head">
          <span class="workspace-index">02</span>
          <div><h2>团队与权限</h2><p>roster、角色和席位状态均以已验证身份为准。</p></div>
        </div>
        <TeamWorkspace />

        <details class="privacy fold">
          <summary>隐私与身份边界</summary>
          <p>Steam 绑定仅用于在比赛中识别你，服务于个人复盘与团队归属；只保存经 Steam 官方验证的 SteamID64，界面仅显示掩码。解绑后进阶权益暂停、历史数据保留，且 7 天内不能重新绑定；团队成员仅可见彼此掩码 ID 与角色，不公开真实 SteamID；分享页不包含任何 SteamID。数据导出/删除接口将在后续版本提供。</p>
        </details>

        <div class="danger">
          <button class="btn btn-ghost" @click="logout">退出登录</button>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.profile-lead { min-width: 0; }
.profile { max-width: 1180px; }
.identity-command {
  margin-top: 22px; display: grid; grid-template-columns: minmax(250px, 1fr) minmax(280px, 1.15fr) minmax(300px, 1fr);
  gap: 0; align-items: stretch; border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: var(--radius-lg);
  background: linear-gradient(108deg, rgba(230,165,74,.07), transparent 38%), var(--bg-panel); overflow: hidden;
}
.identity-command > * { padding: 22px 24px; min-width: 0; }
.identity-command > * + * { border-left: 1px solid var(--border); }

.acct { display: flex; align-items: center; gap: 16px; }
.avatar {
  width: 52px; height: 52px; border-radius: 50%;
  background: var(--accent-soft); color: var(--accent);
  display: flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 700; flex-shrink: 0;
}
.acct-info { min-width: 0; }
.identity-kicker { color: var(--text-muted); font-size: 11px; font-weight: 800; letter-spacing: .12em; margin-bottom: 4px; }
.name { font-size: 18px; font-weight: 700; }
.email { color: var(--text-muted); font-size: 13px; margin-top: 4px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; word-break: normal; }

.row { display: flex; align-items: center; gap: 10px; }
.tier-line { display: flex; align-items: center; gap: 10px; margin-top: 5px; }
.tier-line strong { font-size: 18px; }
.desc { color: var(--text-secondary); font-size: 13px; margin-top: 10px; line-height: 1.6; }
.upgrade { margin-top: 14px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.upgrade-note { color: var(--text-muted); font-size: 12px; }

.stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px 8px; text-align: left; align-content: center; }
.stat { display: flex; flex-direction: column; gap: 2px; }
.stat .num { font-size: 24px; font-weight: 800; line-height: 1.1; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.stat.ok .num { color: var(--ok); } .stat.info .num { color: var(--info); } .stat.err .num { color: var(--err); }
.stat .lbl { font-size: 12px; color: var(--text-muted); }

.sec { font-size: 17px; }
.list { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.list .row { padding: 12px 16px; }
.fname { color: var(--text-primary); font-size: 13.5px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fname:hover { color: var(--accent); }
.map { color: var(--accent); background: var(--accent-soft); padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.empty { color: var(--text-muted); text-align: center; padding: 24px; }

.danger { margin-top: 14px; display: flex; justify-content: flex-end; }

.profile-workspace { margin-top: 28px; display: grid; grid-template-columns: minmax(0, 1.06fr) minmax(360px, .94fr); gap: 34px; align-items: start; }
.profile-main, .profile-side { min-width: 0; display: flex; flex-direction: column; gap: 14px; }
.profile-side { border-left: 1px solid var(--border); padding-left: 28px; }
.workspace-head { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 2px; }
.workspace-index { color: var(--accent); font-size: 12px; font-weight: 800; line-height: 1.7; }
.workspace-head h2 { font-size: 17px; line-height: 1.4; }
.workspace-head p { color: var(--text-muted); font-size: 12px; line-height: 1.6; margin-top: 2px; }
.coach-entry { display: flex; flex-direction: column; gap: 10px; color: var(--text-primary); text-decoration: none; transition: border-color var(--dur-fast) var(--ease); }
.coach-entry:hover { border-color: var(--accent); }
.coach-head { display: flex; align-items: center; gap: 10px; }
.coach-head .sub-h { margin: 0; }
.coach-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; max-width: 76ch; }
.coach-go { color: var(--accent); font-weight: 600; font-size: 13px; margin-top: 2px; }
.privacy { margin-top: 8px; color: var(--text-secondary); font-size: 13px; line-height: 1.7; max-width: 76ch; }
.privacy summary { cursor: pointer; color: var(--text-secondary); font-weight: 600; }
.privacy p { margin-top: 8px; }

@media (max-width: 720px) {
  .identity-command { grid-template-columns: 1fr; }
  .identity-command > * + * { border-left: 0; border-top: 1px solid var(--border); }
  .identity-command > * { padding: 18px 16px; }
  .stats { grid-template-columns: repeat(2, 1fr); row-gap: 14px; }
  .email { white-space: normal; word-break: break-all; }
  .profile-workspace { grid-template-columns: 1fr; gap: 26px; }
  .profile-side { border-left: 0; padding-left: 0; }
  .list .row { flex-wrap: wrap; }
  .fname { flex-basis: 100%; }
}

@media (min-width: 721px) and (max-width: 980px) {
  .identity-command { grid-template-columns: 1fr 1fr; }
  .identity-command .stats { grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--border); grid-template-columns: repeat(4, 1fr); }
  .profile-workspace { grid-template-columns: 1fr; }
  .profile-side { border-left: 0; padding-left: 0; }
}
</style>
