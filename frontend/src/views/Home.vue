<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { authStore } from '../stores/auth'
import { steamStore } from '../stores/steam'
import { mapLabel } from '../utils/mapNames'

const router = useRouter()
const route = useRoute()
const demos = ref([])
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const hasMore = ref(true)
const offset = ref(0)
const PAGE = 10
const query = ref('')
const statusFilter = ref('')
const mapFilter = ref('')
const sort = ref('newest')
let requestGeneration = 0
let searchTimer = null
const mapOptions = computed(() => [...new Set(demos.value.map((d) => d.map_name).filter(Boolean))].sort())
const retrying = ref(new Set())
const view = ref(route.query.view === 'trash' ? 'trash' : 'active')
const tagFilter = ref('')
const editing = ref(null)
const editForm = ref({ display_name: '', tags: '', notes: '', is_favorite: false })
const mutating = ref(new Set())
const hasFilters = computed(() => !!(
  query.value.trim() ||
  tagFilter.value.trim() ||
  (view.value === 'active' && (statusFilter.value || mapFilter.value.trim()))
))

const STATUS = {
  uploaded: { cls: 'pill-muted', label: '已上传' },
  parsing:  { cls: 'pill-info',  label: '解析中' },
  parsed:   { cls: 'pill-ok',    label: '已解析' },
  failed:   { cls: 'pill-err',   label: '失败' },
}

// ---- 档位 / Steam 身份（与壳层同源，第一眼可读）----
const user = computed(() => authStore.state.user)
const TIER_LABEL = { free: '免费版', pro: '进阶版', max: '团队版' }
const effectiveTier = computed(() => {
  const u = user.value
  if (!u || u.subscription_tier === 'free') return 'free'
  if (u.subscription_expire && new Date(u.subscription_expire).getTime() < Date.now()) return 'free'
  return u.subscription_tier
})
const tierLabel = computed(() => TIER_LABEL[effectiveTier.value] || '免费版')
const expired = computed(() => !!user.value && user.value.subscription_tier !== 'free' && effectiveTier.value === 'free')
const steamBound = computed(() => !!steamStore.state.status?.bound)
onMounted(() => { if (user.value) steamStore.load() })

// 主路径：最近一场已解析比赛（demos 按时间倒序 → 取第一条 parsed）
const nextReview = computed(() => {
  if (view.value !== 'active') return null
  return demos.value.find((d) => d.status === 'parsed') || null
})

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function fmtDur(sec) {
  if (sec == null) return null
  const m = Math.round(sec / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : `${m} 分钟`
}

async function load(reset = false) {
  if (reset) { loading.value = true; offset.value = 0; hasMore.value = true }
  else loadingMore.value = true
  error.value = ''
  const generation = ++requestGeneration
  try {
    const params = new URLSearchParams({ limit: PAGE, offset: offset.value, sort: sort.value, view: view.value })
    if (query.value.trim()) params.set('q', query.value.trim())
    if (view.value === 'active' && statusFilter.value) params.set('status', statusFilter.value)
    if (view.value === 'active' && mapFilter.value) params.set('map_name', mapFilter.value)
    if (tagFilter.value.trim()) params.set('tag', tagFilter.value.trim())
    const rows = await api.get(`/api/demos?${params}`)
    if (generation !== requestGeneration) return
    if (reset) demos.value = rows
    else demos.value = [...demos.value, ...rows]
    hasMore.value = rows.length === PAGE
    offset.value += rows.length
  } catch (e) {
    if (generation === requestGeneration) error.value = e.message || '加载失败'
  } finally {
    if (generation === requestGeneration) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

onMounted(() => load(true))
watch([view, statusFilter, sort], () => load(true))
watch([query, mapFilter, tagFilter], () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => load(true), 300)
})
onBeforeUnmount(() => clearTimeout(searchTimer))

function open(d) {
  if (view.value === 'active' && d.status === 'parsed') router.push(`/demo/${d.id}`)
}
function setView(next) {
  view.value = next
  router.replace({ query: { ...route.query, view: next === 'trash' ? 'trash' : undefined } })
}
function setMutating(id, on) { const next = new Set(mutating.value); on ? next.add(id) : next.delete(id); mutating.value = next }
function openEdit(d, event) { event.stopPropagation(); error.value = ''; editing.value = d; editForm.value = { display_name: d.display_name || '', tags: (d.tags || []).join(', '), notes: d.notes || '', is_favorite: !!d.is_favorite } }
function normalizedTags(raw) { return [...new Set(raw.split(',').map((x) => x.trim()).filter(Boolean))] }
async function saveEdit(event) {
  event.stopPropagation(); error.value = ''; const d = editing.value; if (!d) return
  const tags = normalizedTags(editForm.value.tags)
  if (tags.length > 10 || tags.some((x) => x.length < 1 || x.length > 32)) { error.value = '标签最多 10 个，每个 1-32 个字符'; return }
  if (editForm.value.notes.length > 2000) { error.value = '备注不能超过 2000 个字符'; return }
  setMutating(d.id, true)
  try { Object.assign(d, await api.patch(`/api/demos/${d.id}`, { display_name: editForm.value.display_name.trim() || null, tags, notes: editForm.value.notes.trim() || null, is_favorite: editForm.value.is_favorite })); editing.value = null }
  catch (e) { error.value = e.message || '保存失败' }
  finally { setMutating(d.id, false) }
}
async function toggleFavorite(d, event) { event.stopPropagation(); if (mutating.value.has(d.id)) return; error.value = ''; setMutating(d.id, true); try { d.is_favorite = (await api.patch(`/api/demos/${d.id}`, { is_favorite: !d.is_favorite })).is_favorite } catch (e) { error.value = e.message || '操作失败' } finally { setMutating(d.id, false) } }
async function moveTrash(d, event) { event.stopPropagation(); if (mutating.value.has(d.id)) return; error.value = ''; setMutating(d.id, true); try { await api.delete(`/api/demos/${d.id}`); await load(true) } catch (e) { error.value = e.message || '移入回收站失败' } finally { setMutating(d.id, false) } }
async function restore(d, event) { event.stopPropagation(); if (mutating.value.has(d.id)) return; error.value = ''; setMutating(d.id, true); try { await api.post(`/api/demos/${d.id}/restore`); await load(true) } catch (e) { error.value = e.message || '恢复失败' } finally { setMutating(d.id, false) } }
async function retry(d, event) {
  event.stopPropagation()
  if (retrying.value.has(d.id)) return
  retrying.value = new Set(retrying.value).add(d.id)
  try { await api.post(`/api/demos/${d.id}/retry`); d.status = 'uploaded'; d.error_msg = null; await load(true) }
  catch (e) { d.error_msg = e.message || '重新解析失败' }
  finally { const next = new Set(retrying.value); next.delete(d.id); retrying.value = next }
}
</script>

<template>
  <div class="page-wrap">
    <div class="head-row page-head">
      <div>
        <p class="eyebrow">作战记录台</p>
        <h1 class="page-title">我的复盘</h1>
        <p class="page-sub">上传的比赛录像都在这里，点开即可复盘</p>
      </div>
      <router-link to="/upload" class="btn btn-primary">上传 Demo</router-link>
    </div>

    <!-- 权益 / 身份状态条（第一眼可读，不弱化 Free/Pro/Max 与 Steam 语义） -->
    <div v-if="view === 'active'" class="status-strip" aria-label="账户权益与身份状态">
      <span class="ss-item"><span class="ss-k">档位</span><span class="tier-badge" :class="`tier-${effectiveTier}`" :title="expired ? '订阅已过期，已降级为免费版' : ''">{{ expired ? '免费版（已过期）' : tierLabel }}</span></span>
      <span class="ss-sep"></span>
      <span class="ss-item">
        <span class="ss-k">Steam</span>
        <span class="ss-v identity-pill" :class="steamStore.state.loaded ? (steamBound ? 'idp-bound' : 'idp-unbound') : 'idp-unbound'">
          <span class="dot" aria-hidden="true"></span>{{ steamStore.state.loaded ? (steamBound ? '已绑定' : '未绑定') : '加载中…' }}
        </span>
      </span>
      <span class="ss-sep"></span>
      <span class="ss-item">
        <span class="ss-k">身份语义</span>
        <span class="ss-v">{{ effectiveTier === 'max' ? '团队版：支持团队复盘与 roster' : (effectiveTier === 'pro' ? '进阶版：个人复盘需绑定 Steam' : '免费版：每天可上传 1 场') }}</span>
      </span>
    </div>

    <!-- 主路径：继续复盘 / 上传（真实最近一场已解析比赛） -->
    <div v-if="loading && view === 'active' && !nextReview" class="review-cta review-cta-loading" aria-hidden="true">
      <div class="rc-meta"><div class="sk-line w30"></div><div class="sk-line w60"></div><div class="sk-line w30"></div></div>
    </div>
    <div v-else-if="nextReview" class="review-cta">
      <div class="rc-meta">
        <p class="rc-eyebrow">继续复盘</p>
        <p class="rc-title">{{ mapLabel(nextReview.map_name) || nextReview.display_name || nextReview.file_name }}
          <span class="rc-score num">{{ nextReview.team_a_score ?? '—' }}:{{ nextReview.team_b_score ?? '—' }}</span>
        </p>
        <p class="rc-sub">{{ mapLabel(nextReview.map_name) }} · {{ nextReview.rounds ?? '?' }} 回合 · {{ fmtTime(nextReview.created_at) }}</p>
      </div>
      <router-link class="btn btn-primary continue-review" :to="`/demo/${nextReview.id}`">继续复盘 →</router-link>
    </div>

    <div class="filters">
      <div class="view-toggle"><button class="btn" :class="{ 'btn-primary': view === 'active' }" @click="setView('active')">我的复盘</button><button class="btn" :class="{ 'btn-primary': view === 'trash' }" @click="setView('trash')">回收站</button></div>
      <input v-model="query" class="input" placeholder="搜索比赛或玩家…" aria-label="搜索比赛或玩家" />
      <select v-if="view === 'active'" v-model="statusFilter" class="input" aria-label="状态筛选"><option value="">全部状态</option><option value="parsed">已解析</option><option value="parsing">解析中</option><option value="uploaded">已上传</option><option value="failed">失败</option></select>
      <input v-if="view === 'active'" v-model="mapFilter" class="input" list="map-options" placeholder="地图（可输入）" aria-label="地图筛选" />
      <input v-model="tagFilter" class="input" placeholder="标签" aria-label="标签筛选" />
      <datalist id="map-options"><option v-for="m in mapOptions" :key="m" :value="m" /></datalist>
      <select v-model="sort" class="input" aria-label="排序"><option value="newest">最新优先</option><option value="oldest">最早优先</option></select>
    </div>

    <p v-if="error" class="form-error-box mt" role="alert">{{ error }}</p>

    <!-- 加载骨架 -->
    <div v-if="loading" class="list">
      <div v-for="i in 3" :key="i" class="card skeleton"><div class="sk-line w60"></div><div class="sk-line w30"></div></div>
    </div>

    <!-- 空态（诚实） -->
    <div v-else-if="demos.length === 0" class="card empty empty-state">
      <p class="empty-main">{{ hasFilters ? '没有匹配的比赛' : (view === 'trash' ? '回收站为空' : '还没有上传过比赛') }}</p>
      <p class="empty-sub">{{ hasFilters ? '请调整搜索或筛选条件' : (view === 'trash' ? '删除的比赛会显示在这里，可随时恢复' : '上传你的第一场 CS2 比赛录像，就能开始复盘') }}</p>
      <router-link v-if="view === 'active' && !hasFilters" to="/upload" class="btn btn-primary mt16">去上传</router-link>
    </div>

    <!-- 比赛档案（清单） -->
    <div v-else class="deck">
      <div class="section-head">
        <h2>比赛档案</h2>
        <span class="sh-note">共 {{ demos.length }} 条记录</span>
      </div>
      <div class="deck-head" aria-hidden="true">
        <span>比赛</span><span>比分</span><span>时长</span><span>状态</span><span>时间</span><span>操作</span>
      </div>
      <div
        v-for="d in demos" :key="d.id"
        class="card deck-row" :class="{ clickable: view === 'active' && d.status === 'parsed' }"
        @click="open(d)"
      >
        <div class="info">
          <div class="fname"><span class="ellipsis">{{ mapLabel(d.map_name) || (d.display_name || d.file_name) }}</span> <span v-if="d.is_favorite" class="fav" title="已收藏">★</span></div>
          <div class="fmeta">
            <span v-if="d.map_name" class="map-tech">{{ d.map_name }}</span>
            <span v-if="d.status === 'parsed' && d.team_a_name" class="teams">{{ d.team_a_name }} vs {{ d.team_b_name }}</span>
            <span class="original-name">原文件：{{ d.file_name }}</span>
          </div>
          <div v-if="d.tags?.length || d.notes" class="org-meta"><span v-for="tag in d.tags" :key="tag" class="tag">{{ tag }}</span><span v-if="d.notes" class="notes ellipsis">{{ d.notes }}</span></div>
          <p v-if="d.status === 'failed' && view === 'active'" class="fail-msg">{{ d.error_msg || d.parse_warnings || '解析失败' }}</p>
        </div>
        <div class="score-cell num">
          <template v-if="d.status === 'parsed'">{{ d.team_a_score ?? '—' }}:{{ d.team_b_score ?? '—' }} <span class="cell-sub num">{{ d.rounds ?? '—' }} 回合</span></template>
          <span v-else class="cell-muted">—</span>
        </div>
        <div class="dur-cell num">{{ d.status === 'parsed' ? (fmtDur(d.duration_seconds) || '—') : '—' }}</div>
        <div class="status-cell"><span class="pill" :class="STATUS[d.status]?.cls || 'pill-muted'">{{ STATUS[d.status]?.label || d.status }}</span></div>
        <div class="date-cell num">{{ fmtTime(d.created_at) }}</div>
        <div class="right">
          <span v-if="view === 'active' && d.status === 'parsed'" class="enter">复盘 →</span>
          <button v-if="view === 'active' && d.status === 'failed'" class="btn btn-ghost retry-btn" :disabled="retrying.has(d.id)" @click="retry(d, $event)">{{ retrying.has(d.id) ? '提交中…' : '重新解析' }}</button>
          <div class="row-actions"><template v-if="view === 'active'"><button class="btn btn-ghost" :disabled="mutating.has(d.id)" @click="toggleFavorite(d, $event)">{{ d.is_favorite ? '取消收藏' : '收藏' }}</button><button class="btn btn-ghost" :disabled="mutating.has(d.id)" @click="openEdit(d, $event)">编辑</button><button class="btn btn-ghost" :disabled="mutating.has(d.id)" @click="moveTrash(d, $event)">回收站</button></template><button v-else class="btn btn-ghost" :disabled="mutating.has(d.id)" @click="restore(d, $event)">恢复</button></div>
        </div>
      </div>

      <button v-if="hasMore" class="btn more" :disabled="loadingMore" @click="load(false)">
        {{ loadingMore ? '加载中…' : '加载更多' }}
      </button>
    </div>

    <div v-if="editing" class="modal-backdrop" @click.self="editing = null"><div class="modal card" role="dialog" aria-modal="true" aria-label="编辑 Demo"><h3>编辑 Demo</h3><label>显示名称<input v-model="editForm.display_name" class="input" maxlength="255" /></label><label>标签（逗号分隔）<input v-model="editForm.tags" class="input" /></label><label>备注<textarea v-model="editForm.notes" class="input" maxlength="2000" rows="5"></textarea></label><label class="check"><input type="checkbox" v-model="editForm.is_favorite" /> 收藏</label><div class="actions"><button class="btn btn-primary" :disabled="mutating.has(editing.id)" @click="saveEdit">保存</button><button class="btn" @click="editing = null">取消</button></div></div></div>
  </div>
</template>

<style scoped>
.head-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.filters { margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; padding: 0 0 16px; border-bottom: 1px solid var(--border); }
.filters .input { min-width: 150px; flex: 1 1 160px; width: auto; }
.view-toggle { display: flex; gap: 6px; flex: 0 0 auto; }
.mt { margin-top: 14px; }
.mt16 { margin-top: 16px; }

/* 主路径 CTA */
.review-cta {
  display: flex; justify-content: space-between; align-items: center; gap: 18px;
  padding: 16px 20px; margin-top: 16px;
  border: 1px solid var(--border); border-left: 3px solid var(--accent);
  background: var(--app-surface); border-radius: var(--radius);
}
.rc-meta { min-width: 0; }
.rc-eyebrow { font-size: 11px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: var(--text-muted); }
.rc-title { font-size: 17px; font-weight: 800; color: var(--text-primary); margin-top: 3px; }
.rc-score { color: var(--accent); margin-left: 8px; font-variant-numeric: tabular-nums; }
.rc-sub { color: var(--text-muted); font-size: 12.5px; margin-top: 4px; }
.review-cta .btn { flex: 0 0 auto; }
.review-cta-loading { min-height: 110px; }
.review-cta-loading .rc-meta { width: min(340px, 100%); }

/* 比赛档案 deck（表格化、密度克制） */
.deck { margin-top: 18px; }
.deck .section-head { padding: 0 16px 12px; border-bottom: 1px solid var(--border); margin-bottom: 0; }
.deck-head {
  display: grid; grid-template-columns: 2.1fr 1fr .8fr .7fr 1.3fr 1.3fr; gap: 8px 16px;
  padding: 8px 16px; color: var(--text-muted); font-size: 11.5px; font-weight: 600;
}
.deck-row {
  display: grid; grid-template-columns: 2.1fr 1fr .8fr .7fr 1.3fr 1.3fr; gap: 8px 16px; align-items: center;
  padding: 12px 16px; background: transparent; border: none; border-radius: 0;
}
.deck-row + .deck-row { border-top: 1px solid var(--border); }

.info { min-width: 0; }
.fname { display: flex; align-items: baseline; gap: 6px; font-weight: 700; font-size: 15px; line-height: 1.4; min-width: 0; }
.fname .ellipsis { flex: 1 1 auto; }
.fav { color: var(--accent); flex: 0 0 auto; font-size: 12px; }
.fmeta { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 5px; font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.map-tech { color: var(--text-secondary); font-size: 11.5px; font-weight: 600; letter-spacing: .2px; }
.teams { color: var(--text-secondary); font-size: 12px; }
.original-name { color: var(--text-muted); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px; }
.org-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 5px; }
.tag { color: var(--accent); background: var(--accent-soft); padding: 1px 7px; border-radius: 999px; font-size: 11px; white-space: nowrap; }
.notes { color: var(--text-muted); font-size: 11px; max-width: 260px; }
.fail-msg { color: var(--err); font-size: 12px; margin-top: 4px; }

.score-cell, .dur-cell, .date-cell, .status-cell { white-space: nowrap; }
.cell-sub { color: var(--text-muted); font-size: 11px; margin-left: 4px; }
.cell-muted { color: var(--text-faint); }
.deck-row.clickable { cursor: pointer; transition: background var(--dur-fast) var(--ease); }
.deck-row.clickable:hover { background: var(--bg-hover); }
.deck-row:hover .enter { color: var(--accent); }
.enter { color: var(--link); font-size: 12.5px; }

.right { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.row-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; }
.right .retry-btn { padding: 4px 10px; min-height: 0; font-size: 12px; }

.more { margin: 14px auto 0; display: block; }

.empty { text-align: center; padding: 48px 24px; }
.empty-main { margin-top: 12px; font-weight: 600; }
.empty-sub { margin-top: 6px; color: var(--text-muted); font-size: 13px; }

.skeleton .sk-line { height: 12px; border-radius: 4px; background: var(--bg-elevated); margin: 6px 0; }
.w60 { width: 60%; } .w30 { width: 30%; }

.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: grid; place-items: center; z-index: var(--z-modal); padding: 16px; }
.modal { width: min(520px, 100%); display: flex; flex-direction: column; gap: 12px; }
.modal label { display: flex; flex-direction: column; gap: 5px; color: var(--text-secondary); font-size: 13px; }
.check { flex-direction: row !important; align-items: center; }

@media (max-width: 720px) {
  .head-row { flex-direction: column; }
  .review-cta { flex-direction: column; align-items: flex-start; gap: 14px; }
  .review-cta-loading { min-height: 150px; }
  .review-cta .btn { width: 100%; }
  .deck-head { display: none; }
  .deck-row { grid-template-columns: 1fr 1fr; gap: 6px 10px; }
  .deck-row .info { grid-column: 1 / -1; }
  .deck-row .map-tech, .deck-row .original-name, .deck-row .teams { display: none; }
  .deck-row .right { align-items: flex-start; }
  .right .enter { display: none; }
  .row-actions { justify-content: flex-start; }
  .status-cell { justify-self: start; }
}
</style>
