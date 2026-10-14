<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { authStore } from '../stores/auth'
import { api, ApiError } from '../api/client'
import HeatmapCanvas from '../components/HeatmapCanvas.vue'
import PersonalTab from '../components/PersonalTab.vue'
import TeamSyncReplay from '../components/TeamSyncReplay.vue'
import CoordinationInsight from '../components/CoordinationInsight.vue'
import TeamCombatMap from '../components/TeamCombatMap.vue'
// 6R21F-E6A：demo19×Mirage 显式接入真实 NavMesh 连续控制场（单一真值引擎 → MapControlReplay）。
// 仅当 demo id===19 且地图===de_mirage 才使用 Mirage E5N 真值；其它地图不误用，维持原路径且诚实标注。
// 6R21F-E6D：扩展到 demo22×de_inferno（权威 mapName 选择 backend NavMesh/样本，复用同一引擎；不复制一套组件）。
import MapControlReplay from '../components/MapControlReplay.vue'
import { replayConfigFor, isReplayMap, authoritativeMapForDemo } from '../map-control/mapCatalog.js'
import { isPro, tierInfo } from '../utils/subscription'
import { mapLabel } from '../utils/mapNames'
import { renderMarkdown } from '../utils/markdown'

const route = useRoute()
const demoId = route.params.id

const report = ref(null)
const grenades = ref(null)
const teamStats = ref(null)
const entitlement = ref(null)
const identity = ref(null)
const loading = ref(true)
const error = ref('')
const activeTab = ref('overview')
// 6R21F-F3：概览「回合证据」真值源（/rounds：side_a/side_b/kills_count/grenades_count；winner 恒为 null，绝不虚构）
const roundsData = ref(null)
const roundsLoading = ref(false)
const roundsError = ref('')
// 6R18：团队区块结构化状态（绝不因 403/409/网络失败而静默空白）
// status: idle | loading | ready | roster | artifact | network | error
const teamGateStatus = ref('idle')
const teamGateInfo = ref(null)
const teamSyncRef = ref(null)
// 6R15：来自「长期教练」证据跳转 → 个人战术回放定位（透传给 PersonalTab → PersonalTacticalReplay）
const personalLocate = ref(null)
const sourceDownloading = ref(false)
const shareOpen = ref(false)
const shareLoading = ref(false)
const shareInfo = ref(null)
const shareUrl = ref('')
const shareError = ref('')
const shareHours = ref(24)
const shareCopied = ref(false)

const user = computed(() => authStore.state.user)
const pro = computed(() => isPro(user.value))
// 服务端权威 entitlement：team_analysis 为 Max-only（Free/Pro 均锁定且零团队请求）
const teamAllowed = computed(() => entitlement.value?.features?.team_analysis || false)

// ---- 6R21F-E6D：demo×地图连续控制场（权威 mapName 门控；demo19×Mirage 与 demo22×Inferno 接入）----
const e5n = reactive({ sample: null, nav: null, err: '', map: '' })
// 权威对齐：demoId 的权威地图必须 == 本场比赛地图，且该地图已接入（否则不误用，fail-closed）。
const isMapControlDemo = computed(() => {
  const map = report.value?.match?.map_name
  if (!map) return false
  return authoritativeMapForDemo(demoId) === map && isReplayMap(map)
})
const e5nMap = computed(() => (isMapControlDemo.value ? report.value?.match?.map_name : ''))
// 6R21F-E6A：始终启用两套团队复盘视图（团队协作/区域控制），由 teamView 子切换决定同一时刻只挂载其一。
const e6aMount = 'D'
// E6A-MEM/LIFECYCLE：真实产品子切换「团队协作 / 区域控制」，同一时刻只挂载/运行一个回放（消除双 60fps 循环并发）。
// 默认 'replay'（保留既有 6R18 TeamSyncReplay 默认行为）；'mapcontrol' 仅已接入 demo×地图 提供。
const teamView = ref('replay')
const showTeamSync = computed(() => teamView.value === 'replay' && (e6aMount === 'B' || e6aMount === 'D'))
const showMcr = computed(() => teamView.value === 'mapcontrol' && isMapControlDemo.value && (e6aMount === 'C' || e6aMount === 'D'))
async function loadE5n() {
  e5n.sample = null; e5n.nav = null; e5n.err = ''; e5n.map = ''
  if (!isMapControlDemo.value) return
  const rc = replayConfigFor(report.value?.match?.map_name)
  e5n.map = report.value?.match?.map_name
  if (!rc) { e5n.err = '该地图未接入'; return }
  try {
    const [sr, nr] = await Promise.all([fetch(rc.sampleUrl), fetch(rc.navUrl)])
    if (!sr.ok) throw new Error('样本 HTTP ' + sr.status)
    if (!nr.ok) throw new Error('NavMesh HTTP ' + nr.status)
    e5n.sample = await sr.json(); e5n.nav = await nr.json()
  } catch (err) {
    e5n.err = String(err && err.message || err)
  }
}
watch(report, (r) => { if (r && isMapControlDemo.value && (e6aMount === 'C' || e6aMount === 'D')) loadE5n() })

// ---- 6R14：身份识别（绑定 Steam 自动识别「这是你」）----
const youPlayerId = computed(() => identity.value?.you_player_id || null)
const youPlayerName = computed(() => {
  const id = youPlayerId.value
  if (id == null) return ''
  return (report.value?.players || []).find((p) => p.player_id === id)?.name || ''
})
const identityNotice = computed(() => {
  const r = identity.value?.resolution
  if (!r) return null
  if (r === 'identified') return { tone: 'ok', text: `已自动识别到你：${youPlayerName.value || '本场玩家'}（这是你）。` }
  if (r === 'not_in_demo') return { tone: 'warn', text: '这场没有识别到你（你的 Steam 不在本场玩家名单）。' }
  if (r === 'unbound') return { tone: 'warn', text: '未绑定 Steam；绑定后可自动识别我的复盘。' }
  if (r === 'unparsed') return { tone: 'muted', text: '该场尚未解析完成，暂无法识别你的身份。' }
  return null
})
async function loadIdentity() {
  try { identity.value = await api.get(`/api/demos/${demoId}/identity`) } catch { identity.value = null }
}

async function loadEntitlement() {
  try { entitlement.value = await api.get('/api/entitlement') } catch { /* 忽略 */ }
}

// 仅保留击杀（全队）战斗热力密度；纯路线热力图（route_*）不再作为展示模式。
const heatmapModes = [
  { key: 'kills_all', label: '击杀（全队）', pro: false },
]
const activeMode = ref('kills_all')
const opacity = ref(0.9)
const radius = ref(1)

const GRENADE_LABEL = {
  smoke: '烟雾弹', flash: '闪光弹', he: '手雷', molotov: '燃烧瓶', incendiary: '燃烧弹', decoy: '诱饵弹', other: '其他',
}

// 锁定态：free 不请求受限资源（锁定先于请求）
const locked = (requiresPro) => requiresPro && !pro.value

// 所属阵营不作为玩家固定属性展示：换边后该字段会误导用户。
const scoreboardColumns = ['#', '玩家', 'K', 'D', 'A', '爆头率', '伤害', 'ADR', '首杀']
const sortedPlayers = computed(() => {
  const rows = [...(report.value?.players || [])]
  return rows.sort((a, b) => b.stats.adr - a.stats.adr)
})

const grenadeByType = computed(() => grenades.value?.by_type || [])
const grenadeByPlayer = computed(() => grenades.value?.by_player || [])
const grenadeByRound = computed(() => grenades.value?.by_round || [])
const teamRows = computed(() => teamStats.value?.teams || [])

// 概览结论：只用已有比分/队伍名推断（无新增数据，不编造）
const winnerLabel = computed(() => {
  const m = report.value?.match
  if (!m) return ''
  const a = m.team_a_score ?? m.score_ct, b = m.team_b_score ?? m.score_t
  if (a == null || b == null) return ''
  if (a === b) return `双方 ${a}:${b} 战平`
  const hi = Math.max(a, b), lo = Math.min(a, b)
  const winName = a > b ? (m.team_a_name || '队伍 A') : (m.team_b_name || '队伍 B')
  return `${winName} 以 ${hi}:${lo} 取胜`
})

// 6R17：概览「关键观察」——只用已有 match/players 统计诚实推断（数据观察，非因果结论，不编造）
const keyObservations = computed(() => {
  const obs = []
  const m = report.value?.match
  const rows = sortedPlayers.value
  if (!m || !rows.length) return obs
  // 主结论（比分胜负）由 conclusion-strip 的 cs-title 呈现；本场观察只列数据要点
  const top = rows[0]
  if (top?.stats?.adr != null) obs.push({ kind: 'top', tone: 'accent', title: `${top.name} 以 ${top.stats.adr.toFixed(1)} ADR 领跑全场` })
  const topFirst = [...rows].sort((a, b) => (b.stats?.first_kills || 0) - (a.stats?.first_kills || 0))[0]
  if (topFirst?.stats?.first_kills > 0) obs.push({ kind: 'entry', tone: 'info', title: `${topFirst.name} 拿下 ${topFirst.stats.first_kills} 次首杀` })
  const topHs = [...rows].filter((r) => r.stats?.hs_rate != null).sort((a, b) => b.stats.hs_rate - a.stats.hs_rate)[0]
  if (topHs?.stats?.hs_rate >= 0.5) obs.push({ kind: 'aim', tone: 't', title: `${topHs.name} 爆头率 ${(topHs.stats.hs_rate * 100).toFixed(0)}%` })
  if (m.rounds != null && m.duration_seconds != null) obs.push({ kind: 'scale', tone: 'muted', title: `${m.rounds} 回合 · ${Math.round(m.duration_seconds / 60)} 分钟` })
  return obs
})

// ---- 6R21F-F3：回合证据（真值 /rounds，结论优先的证据时间线）----
// /rounds 的 winner 字段恒为 null（后端模型无胜方字段），因此绝不虚构每回合胜负；
// 每回合只展示首帧真实数据：round/side_a/side_b/kills_count/grenades_count。
const roundWinnerLabel = { CT: 'CT 胜', T: 'T 胜' }
const roundEvidence = computed(() => {
  const rs = roundsData.value?.rounds || []
  return rs.map((r) => ({
    round: r.round,
    sideA: r.side_a, sideB: r.side_b,
    kills: r.kills_count ?? 0,
    grenades: r.grenades_count ?? 0,
    // 诚实：winner 为 null（未证明）时显示「未证明」，绝不虚构胜负。
    winner: r.winner || null,
  }))
})
const maxRoundKills = computed(() => {
  let m = 0
  for (const r of roundEvidence.value) if (r.kills > m) m = r.kills
  return m || 1
})
async function loadRounds() {
  roundsLoading.value = true
  roundsError.value = ''
  try {
    roundsData.value = await api.get(`/api/demos/${demoId}/rounds`)
  } catch (e) {
    roundsData.value = null
    roundsError.value = e instanceof ApiError ? e.message : '回合证据加载失败'
  } finally {
    roundsLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    await loadEntitlement()   // 先取 entitlement（团队权限权威）
    await loadIdentity()      // 6R14：自动识别「这是你」
    report.value = await api.get(`/api/demos/${demoId}/report/basic`)
    // 6R21F-F3：回合证据独立加载（不影响主报告；失败仅降级该节，不虚构）
    await loadRounds()
    // 道具按 pro 请求；团队为 Max-only（features.team_analysis），锁定态不请求
    if (pro.value) {
      grenades.value = await api.get(`/api/demos/${demoId}/grenades`)
    }
    // 团队为 Max-only（features.team_analysis）；其失败（如 roster 不满）不得阻塞整份复盘（概览/个人仍可用）
    if (teamAllowed.value) {
      await loadTeamStats()
    }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

// 6R18：团队区块门禁错误结构化（区分 roster 不足 / artifact 缺失 / 网络失败，绝不同时只置 null 导致空白）。
const GATE_ROSTER_CODES = new Set(['TEAM_GATE_LOW', 'TEAM_NOT_MEMBER', 'TEAM_ROSTER_SIZE', 'TEAM_CALLER_NOT_IN_DEMO', 'SUBSCRIPTION_REQUIRED'])
const GATE_ARTIFACT_CODES = new Set(['DEMO_NOT_PARSED', 'TACTICAL_UNAVAILABLE'])
function applyTeamGateError(e) {
  if (!(e instanceof ApiError)) {
    teamGateStatus.value = 'error'
    teamGateInfo.value = { kind: 'error', code: 'UNKNOWN', message: '团队数据加载失败', matched: null, matchedRequired: 3, retryable: true }
    return
  }
  const code = e.code
  let status = 'error'
  if (e.status === 0 || code === 'NETWORK_ERROR' || code === 'TIMEOUT') status = 'network'
  else if (e.status === 409 || GATE_ARTIFACT_CODES.has(code)) status = 'artifact'
  else if (e.status === 403 || GATE_ROSTER_CODES.has(code)) status = 'roster'
  const matched = /当前\s*(\d+)\s*人/.exec(e.message || '')
  teamGateStatus.value = status
  teamGateInfo.value = {
    kind: status, code, message: e.message || '无法加载团队分析',
    matched: matched ? Number(matched[1]) : null, matchedRequired: 3,
    retryable: status === 'network' || status === 'error' || status === 'artifact',
  }
}
async function loadTeamStats() {
  teamGateStatus.value = 'loading'
  teamGateInfo.value = null
  teamStats.value = null
  try {
    teamStats.value = await api.get(`/api/demos/${demoId}/team-stats`)
    teamGateStatus.value = 'ready'
  } catch (e) {
    teamStats.value = null
    applyTeamGateError(e)
  }
}
function retryTeam() { if (teamAllowed.value) loadTeamStats() }

async function exportJson() {
  try {
    const sections = pro.value ? 'match,players,rounds,kills,grenades' : 'match,players,rounds,kills'
    const blob = await api.getBlob(`/api/demos/${demoId}/export.json?sections=${sections}`)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `demo-${demoId}.json`; a.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (e) { error.value = e instanceof ApiError ? e.message : '导出失败' }
}
async function exportXlsx() {
  try {
    const sections = pro.value ? 'match,players,rounds,kills,grenades' : 'match,players,rounds,kills'
    const blob = await api.getBlob(`/api/demos/${demoId}/export.xlsx?sections=${sections}`)
    const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `demo-${demoId}.xlsx`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (e) { error.value = e instanceof ApiError ? e.message : '导出失败' }
}

function safeDemoFileName(value) {
  const leaf = String(value || '').replace(/\\/g, '/').split('/').pop()
    .replace(/[\u0000-\u001f\u007f]/g, '').trim()
  if (!leaf) return `demo-${demoId}.dem`
  return leaf.toLowerCase().endsWith('.dem') ? leaf : `${leaf}.dem`
}

// 深度配合分析 → 定位到团队同步回放（切回合 + scrub 到秒 + 开图层 + 高亮相关玩家）
function locateReplay(opts) {
  if (!teamSyncRef.value) return
  teamSyncRef.value.locate(opts.round, opts.time, {
    layers: opts.layers || [],
    highlight: opts.highlight || [],
  })
}

// ---- 6R15：从「长期教练」证据跳转而来（?locate=<json>&view=team|personal）----
// view=personal → 切到「个人复盘」并把定位请求透传给 PersonalTab/PersonalTacticalReplay；
// view=team     → 切到「团队复盘」并等待 TeamSyncReplay 就绪后调用其 locate。
async function waitFor(predicate, timeoutMs = 8000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (predicate()) return true
    await nextTick()
    await new Promise((r) => setTimeout(r, 60))
  }
  return false
}

function coachLocatePayload() {
  const raw = route.query.locate
  if (!raw) return null
  let req
  try { req = JSON.parse(raw) } catch { return null }
  if (req == null || typeof req !== 'object') return null
  return {
    view: route.query.view === 'personal' ? 'personal' : 'team',
    round: Number(req.round),
    time: req.time == null ? null : Number(req.time),
    layers: Array.isArray(req.layers) ? req.layers : [],
    highlight: Array.isArray(req.highlight) ? req.highlight : [],
  }
}

function applyCoachLocate(loc) {
  if (!loc) return
  activeTab.value = loc.view === 'personal' ? 'my' : 'team'
  if (loc.view === 'personal') {
    personalLocate.value = { round: loc.round, time: loc.time, layers: loc.layers, highlight: loc.highlight }
  }
}

async function flushCoachLocate() {
  const loc = coachLocatePayload()
  if (!loc) return
  applyCoachLocate(loc)
  if (loc.view === 'personal') {
    // 已通过 personalLocate ref 透传；PersonalTab/PersonalTacticalReplay 会在数据就绪后落地。
    return
  }
  // 团队：等待 TeamSyncReplay 挂载后调用 locate（其内部负责切回合+seek+图层+高亮）
  const ok = await waitFor(() => !!teamSyncRef.value)
  if (ok && teamSyncRef.value) {
    teamSyncRef.value.locate(loc.round, loc.time, { layers: loc.layers, highlight: loc.highlight })
  }
}

async function downloadSource() {
  if (sourceDownloading.value) return
  sourceDownloading.value = true
  error.value = ''
  try {
    const blob = await api.getBlob(`/api/demos/${demoId}/source`)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = safeDemoFileName(report.value?.demo?.file_name)
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '原始 Demo 下载失败'
  } finally {
    sourceDownloading.value = false
  }
}

async function openShareDialog() {
  shareOpen.value = true
  shareLoading.value = true
  shareError.value = ''
  shareUrl.value = ''
  shareCopied.value = false
  try {
    shareInfo.value = (await api.get(`/api/demos/${demoId}/share`)).share
  } catch (e) {
    shareError.value = e instanceof ApiError ? e.message : '分享状态加载失败'
  } finally {
    shareLoading.value = false
  }
}

function closeShareDialog() {
  shareOpen.value = false
  shareUrl.value = ''
  shareCopied.value = false
}

async function createShare() {
  if (shareLoading.value) return
  shareLoading.value = true
  shareError.value = ''
  shareCopied.value = false
  try {
    const data = await api.post(`/api/demos/${demoId}/share`, { expires_in_hours: Number(shareHours.value) })
    shareInfo.value = { status: data.status, created_at: data.created_at, expires_at: data.expires_at, revoked_at: data.revoked_at }
    shareUrl.value = `${window.location.origin}/share#${data.token}`
  } catch (e) {
    shareError.value = e instanceof ApiError ? e.message : '分享链接生成失败'
  } finally {
    shareLoading.value = false
  }
}

async function copyShare() {
  if (!shareUrl.value) return
  shareError.value = ''
  try {
    await navigator.clipboard.writeText(shareUrl.value)
    shareCopied.value = true
  } catch {
    shareError.value = '浏览器未允许复制，请手动选中链接复制'
  }
}

async function revokeShare() {
  if (shareLoading.value) return
  shareLoading.value = true
  shareError.value = ''
  try {
    await api.delete(`/api/demos/${demoId}/share`)
    shareInfo.value = { ...(shareInfo.value || {}), status: 'revoked' }
    shareUrl.value = ''
    shareCopied.value = false
  } catch (e) {
    shareError.value = e instanceof ApiError ? e.message : '撤销失败'
  } finally {
    shareLoading.value = false
  }
}

// ---------- AI 诊断（Phase 5A） ----------
const aiEntitlement = ref(null)
const aiReport = ref(null)        // 当前展示的报告（详情）
const aiGenerating = ref(false)
const aiError = ref('')
let aiTimer = null

const aiHtml = computed(() => (aiReport.value?.markdown ? renderMarkdown(aiReport.value.markdown) : ''))
const aiLocked = computed(() => !aiEntitlement.value?.can_generate)

// ---------- 6R11.2：AI 报告真实性/时效 trust 判定（通用规则，不针对 demo/字符串硬编码） ----------
const AI_TRUST_REASON_CN = {
  fake_provider: '该报告由开发测试模板（fake provider）生成',
  template_provider: '该报告使用模板模型（deterministic-template-v1）生成',
  stale_snapshot: '该报告基于已过期的比赛数据',
  unresolved_placeholders: '报告正文含未填充的内部模板键',
  metrics_mismatch: '报告数据与当前比赛信息不一致',
  no_report: '尚无报告',
}

// 前端自足判定（无论后端是否已更新 trust 字段都生效）；有后端 trust 则并入
function aiReportTrust(report, match) {
  const reasons = []
  if (!report?.id) { reasons.push('no_report'); return { trusted: false, reasons } }
  if (report.provider === 'fake') reasons.push('fake_provider')
  if (report.model === 'deterministic-template-v1') reasons.push('template_provider')
  const md = report.markdown || ''
  if (/\[[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+]/.test(md)) reasons.push('unresolved_placeholders')
  // 报告所基于的 match 与当前比赛信息比对（后端暴露 metrics_summary 时）
  // 注意：快照的 match 以 CT/T 视角（score_ct/score_t）存储；与 match.score_ct/score_t 同口径比较，
  // 不可与 A/B 视角 team_a_score/team_b_score 混用（换边局二者不同，会误判为不一致）。
  const ms = report.metrics_summary
  if (ms && match) {
    if (ms.map_name && match.map_name && ms.map_name !== match.map_name) reasons.push('metrics_mismatch')
    const curScore = [match.score_ct, match.score_t]
    if (ms.score_ct != null && ms.score_t != null &&
        !(Number(ms.score_ct) === Number(curScore[0]) && Number(ms.score_t) === Number(curScore[1]))) reasons.push('metrics_mismatch')
    if (ms.rounds != null && match.rounds != null && Number(ms.rounds) !== Number(match.rounds)) reasons.push('metrics_mismatch')
  }
  if (report.trust && !report.trust.trusted) {
    for (const r of report.trust.reasons) if (!reasons.includes(r)) reasons.push(r)
  }
  return { trusted: reasons.length === 0, reasons }
}

const aiTrust = computed(() => aiReportTrust(aiReport.value, report.value?.match))
const aiSuspectReasons = computed(() => (aiTrust.value.trusted ? [] : aiTrust.value.reasons))
const aiSuspectText = computed(() => {
  const n = aiSuspectReasons.value.length
  if (!n) return ''
  const head = n > 1 ? `${n} 项原因` : AI_TRUST_REASON_CN[aiSuspectReasons.value[0]] || '该报告不可用于正式结论'
  return head
})

async function loadAiEntitlement() {
  try {
    aiEntitlement.value = await api.get(`/api/demos/${demoId}/ai-report/entitlement`)
  } catch { /* 权限查询失败不阻塞主报告 */ }
}

async function loadLatestAiReport() {
  try {
    const list = await api.get(`/api/demos/${demoId}/ai-report`)
    if (list.length > 0) {
      const latest = list[0]
      if (latest.status === 'succeeded' || latest.status === 'failed') {
        aiReport.value = await api.get(`/api/demos/${demoId}/ai-report/${latest.id}`)
        aiGenerating.value = false
        stopAiPoll()
        return
      }
      // pending/running：开始轮询
      aiReport.value = { ...latest }
      aiGenerating.value = true
      startAiPoll()
    }
  } catch { /* 无报告或查询失败 */ }
}

function startAiPoll() {
  stopAiPoll()
  aiTimer = setInterval(async () => {
    try {
      const list = await api.get(`/api/demos/${demoId}/ai-report`)
      const latest = list[0]
      if (!latest || latest.id !== aiReport.value?.id) return
      if (latest.status === 'succeeded' || latest.status === 'failed') {
        aiReport.value = await api.get(`/api/demos/${demoId}/ai-report/${latest.id}`)
        aiGenerating.value = false
        stopAiPoll()
      } else {
        aiReport.value = { ...latest }
      }
    } catch { /* 轮询失败继续下一轮 */ }
  }, 2000)
}
function stopAiPoll() { if (aiTimer) { clearInterval(aiTimer); aiTimer = null } }

async function generateAiReport() {
  aiError.value = ''
  aiGenerating.value = true
  try {
    const resp = await api.post(`/api/demos/${demoId}/ai-report`)
    aiReport.value = resp.report
    if (resp.report.status === 'succeeded') {
      aiReport.value = await api.get(`/api/demos/${demoId}/ai-report/${resp.report.id}`)
      aiGenerating.value = false
    } else if (resp.report.status === 'failed') {
      aiReport.value = resp.report
      aiGenerating.value = false
    } else {
      startAiPoll()
    }
  } catch (e) {
    aiError.value = e instanceof ApiError ? e.message : '生成失败，请重试'
    aiGenerating.value = false
  }
}

async function retryAiReport() {
  if (!aiReport.value?.id) return
  aiError.value = ''
  aiGenerating.value = true
  try {
    const resp = await api.post(`/api/demos/${demoId}/ai-report/${aiReport.value.id}/retry`)
    aiReport.value = resp.report
    if (resp.report.status === 'succeeded') {
      aiReport.value = await api.get(`/api/demos/${demoId}/ai-report/${resp.report.id}`)
      aiGenerating.value = false
    } else if (resp.report.status !== 'failed') {
      startAiPoll()
    } else {
      aiGenerating.value = false
    }
  } catch (e) {
    aiError.value = e instanceof ApiError ? e.message : '重试失败'
    aiGenerating.value = false
  }
}

onMounted(async () => {
  await load()
  loadAiEntitlement()
  if (pro.value) loadLatestAiReport()
  flushCoachLocate()
})
// 6R15：路由带 ?locate= 时（直接进入或证据跳转）解析并驱动定位
watch(() => route.query.locate, () => { flushCoachLocate() })
onBeforeUnmount(() => { stopAiPoll() })
</script>

<template>
  <div class="page-wrap report">
    <div v-if="loading" class="card">加载中…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else-if="report">
      <!-- 工作台上下文条：地图/比分/队伍/回合/时长/身份/权益（保留 .head 以兼容既有 E2E） -->
      <div class="head wb-context">
        <div class="wb-main">
          <div class="map-line wb-map">
            <span class="map-name">{{ mapLabel(report.match.map_name) }} <span class="map-tech">{{ report.match.map_name }}</span></span>
          </div>
          <div class="score-row wb-score-row">
            <span class="wb-side ct"><span class="wb-team-tag" aria-hidden="true"></span><span class="team team-a">{{ report.match.team_a_name || '队伍 A' }}</span></span>
            <span class="score wb-score num">
              {{ report.match.team_a_score ?? report.match.score_ct }} : {{ report.match.team_b_score ?? report.match.score_t }}
            </span>
            <span class="wb-side t"><span class="wb-team-tag" aria-hidden="true"></span><span class="team team-b">{{ report.match.team_b_name || '队伍 B' }}</span></span>
          </div>
          <div class="meta wb-meta">
            <span>{{ Math.round(report.match.duration_seconds / 60) }} 分钟</span>
            <span class="dot">·</span>
            <span>{{ report.match.rounds }} 回合 (R1–R{{ report.match.rounds }})</span>
            <!-- 6R17.2：身份状态融入紧凑页头（保留可见性与语义，不再占全宽横幅） -->
            <span v-if="identityNotice" class="id-status identity-banner" :class="`idb-${identityNotice.tone}`">
              <span class="id-status-text">{{ identityNotice.text }}</span>
              <router-link v-if="identity.value?.resolution === 'unbound'" to="/profile" class="idb-link">去绑定</router-link>
            </span>
          </div>
        </div>
        <div class="wb-status">
          <div class="wb-side-actions">
            <span v-if="!pro" class="pill pill-muted">免费版</span>
            <span v-else class="pill pill-ok">{{ tierInfo(user).label }}</span>
            <button class="btn btn-ghost share-btn" @click="openShareDialog">分享</button>
          </div>
        </div>
      </div>

      <div v-if="shareOpen" class="share-backdrop" @click.self="closeShareDialog">
        <div class="share-dialog card" role="dialog" aria-modal="true" aria-label="私密分享">
          <div class="share-title-row"><h3>私密分享</h3><button class="share-close" aria-label="关闭" @click="closeShareDialog">×</button></div>
          <p class="share-note">链接仅展示匿名比赛概览与玩家统计，不包含昵称、SteamID、坐标、文件名或 Demo 下载。生成新链接会立即使旧链接失效。</p>
          <p v-if="shareInfo?.status === 'active' && !shareUrl" class="share-existing">当前已有有效链接。出于安全，明文链接不会保存；如需再次复制，请重新生成。</p>
          <label class="share-label">有效期
            <select v-model.number="shareHours" class="input">
              <option :value="24">24 小时</option><option :value="72">3 天</option><option :value="168">7 天</option>
            </select>
          </label>
          <div v-if="shareUrl" class="share-link-row">
            <input class="input share-link" :value="shareUrl" readonly @focus="$event.target.select()" />
            <button class="btn btn-primary" @click="copyShare">{{ shareCopied ? '已复制' : '复制链接' }}</button>
          </div>
          <p v-if="shareInfo?.expires_at" class="share-expiry">状态：{{ shareInfo.status }} · 到期：{{ new Date(shareInfo.expires_at).toLocaleString('zh-CN') }}</p>
          <div v-if="shareError" class="alert alert-error">{{ shareError }}</div>
          <div class="share-actions">
            <button class="btn btn-primary" :disabled="shareLoading" @click="createShare">{{ shareLoading ? '处理中…' : (shareInfo?.status === 'active' ? '重新生成' : '生成链接') }}</button>
            <button v-if="shareInfo?.status === 'active'" class="btn btn-secondary" :disabled="shareLoading" @click="revokeShare">撤销链接</button>
            <button class="btn btn-ghost" @click="closeShareDialog">关闭</button>
          </div>
        </div>
      </div>

      <!-- Tabs（一级收敛：概览 / 个人复盘 / 团队复盘 / AI 教练；保留 .tabs 以兼容既有 E2E） -->
      <div class="tabs wb-tabs">
        <button v-for="t in [
          { key: 'overview', label: '概览' },
          { key: 'my', label: '个人复盘' },
          { key: 'team', label: '团队复盘' },
          { key: 'ai', label: 'AI 教练' },
        ]" :key="t.key" class="tab" :class="{ on: activeTab === t.key }" @click="activeTab = t.key">
          {{ t.label }}
        </button>
      </div>

      <!-- 个人复盘（含个人热区/击杀死亡/道具，Player-first） -->
      <section v-if="activeTab === 'my'">
        <PersonalTab :demo-id="demoId" :map-name="report.match.map_name" :locate-request="personalLocate" />
      </section>

      <!-- 概览 -->
      <section v-if="activeTab === 'overview'">
        <div class="conclusion conclusion-strip verdict-strip">
          <div class="cs-score vs-score">
            <span class="cs-num vs-num">{{ report.match.team_a_score ?? report.match.score_ct }}</span>
            <span class="cs-colon vs-colon">:</span>
            <span class="cs-num vs-num">{{ report.match.team_b_score ?? report.match.score_t }}</span>
          </div>
          <div class="cs-body vs-body">
            <div class="cs-title vs-title">{{ winnerLabel }}</div>
            <div class="cs-desc vs-desc">{{ mapLabel(report.match.map_name) }} · {{ report.match.rounds }} 回合 · {{ Math.round(report.match.duration_seconds / 60) }} 分钟</div>
          </div>
          <div class="cs-actions vs-actions">
            <button class="btn btn-primary" @click="activeTab = 'my'">进入个人复盘 →</button>
            <button class="btn btn-secondary" @click="activeTab = 'team'">团队复盘</button>
          </div>
        </div>

        <!-- 回合证据：真值 /rounds 证据时间线（结论可点到哪一秒的“时刻”锚点；不虚构每回合胜负） -->
        <div class="round-evidence" data-testid="round-evidence">
          <div class="wb-section-head">
            <h2>回合证据</h2>
            <span class="sh-note">来自本场真实数据 · 每回合击杀/道具分布</span>
          </div>
          <div v-if="roundsLoading" class="re-wb-loading">回合证据加载中…</div>
          <div v-else-if="roundsError" class="re-wb-error">{{ roundsError }}</div>
          <template v-else-if="roundEvidence.length">
            <div class="re-track" role="list" aria-label="回合证据">
              <div v-for="r in roundEvidence" :key="r.round" class="re-round" role="listitem" :class="{ 'is-highlight': r.round === report.match.rounds }" :title="`R${r.round}：击杀 ${r.kills} · 道具 ${r.grenades}`">
                <span class="re-no">R{{ r.round }}</span>
                <span class="re-kills num">{{ r.kills }}<small>杀</small></span>
                <span class="re-bar" aria-hidden="true"><i :style="{ width: Math.max(8, (r.kills / maxRoundKills) * 100) + '%' }"></i></span>
                <span class="re-side">
                  <span v-if="r.sideA" class="tag tag-ct">{{ r.sideA }}</span>
                  <span v-if="r.sideB" class="tag tag-t">{{ r.sideB }}</span>
                </span>
              </div>
            </div>
            <p class="re-note">每回合只展示真实可证明数据（击杀人/道具数/CT·T 阵营）。该数据源的每回合胜方不可证明，故不标注“×回合 谁胜”；最终胜负见上方裁决。</p>
          </template>
          <div v-else class="re-empty wb-empty-note">本场暂无回合证据（可能尚未解析完成）。</div>
        </div>

        <!-- 关键观察：数据驱动、诚实推断（结论 → 证据 → 回放动作） -->
        <div v-if="keyObservations.length" class="key-observations section">
          <div class="section-head">
            <h2>本场观察</h2>
            <span class="sh-note">来自本场数据 · 非因果结论</span>
          </div>
          <ul class="obs-list">
            <li v-for="(o, i) in keyObservations.slice(0, 4)" :key="i" class="obs-line" :class="[`obs-${o.tone}`, { lead: i === 0 }]">
              <span class="obs-dot" aria-hidden="true"></span>
              <span class="obs-text">{{ o.title }}</span>
            </li>
          </ul>
        </div>

        <div class="card table-card">
          <div class="table-scroll">
            <table class="tbl">
              <thead>
                <tr><th v-for="c in scoreboardColumns" :key="c">{{ c }}</th></tr>
              </thead>
              <tbody>
                <tr v-for="(p, i) in sortedPlayers" :key="p.steam_id" :class="{ 'me-row': p.player_id === youPlayerId }">
                  <td class="num">{{ i + 1 }}</td>
                  <td class="pname">{{ p.name }} <span v-if="p.player_id === youPlayerId" class="me-badge">这是你</span></td>
                  <td class="num">{{ p.stats.kills }}</td>
                  <td class="num">{{ p.stats.deaths }}</td>
                  <td class="num">{{ p.stats.assists }}</td>
                  <td class="num">{{ Math.round(p.stats.hs_rate * 100) }}%</td>
                  <td class="num">{{ p.stats.damage }}</td>
                  <td class="num adr">{{ p.stats.adr.toFixed(1) }}</td>
                  <td class="num">{{ p.stats.first_kills }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 技术诊断 / 导出：保留击杀（全队）战斗热力 + 道具聚合；原始击杀表/回合 tick 表/路线热力图已移除 -->
      <details class="card diag-drawer">
        <summary>技术诊断 / 导出</summary>

      <!-- 导出（Power-user 操作，不占用主视界） -->
      <section class="export-section">
        <div class="export-row">
          <span class="export-label">导出原始</span>
          <button class="btn btn-ghost" @click="exportJson">JSON</button>
          <button class="btn btn-ghost" @click="exportXlsx">XLSX</button>
          <button class="btn btn-ghost" :disabled="sourceDownloading" @click="downloadSource">
            {{ sourceDownloading ? '准备中…' : '源文件' }}
          </button>
        </div>
      </section>

      <section>
        <div class="card hm-card">
          <div class="hm-modes">
            <button v-for="m in heatmapModes" :key="m.key" class="mode-btn"
              :class="{ on: activeMode === m.key }" @click="activeMode = m.key">
              {{ m.label }}
            </button>
          </div>
          <HeatmapCanvas
            :demo-id="demoId"
            :map-name="report.match.map_name"
            :mode="activeMode"
            :opacity="opacity"
            :radius="radius"
            :fallback-src="`/api/demos/${demoId}/heatmap/${activeMode}`"
          />
          <!-- 真实控件：透明度 / 半径 -->
          <div class="controls">
            <label class="ctl">
              <span>透明度</span>
              <input type="range" min="0.2" max="1" step="0.05" v-model.number="opacity" />
              <span class="num">{{ Math.round(opacity * 100) }}%</span>
            </label>
            <label class="ctl">
              <span>扩散半径</span>
              <input type="range" min="0" max="6" step="1" v-model.number="radius" />
              <span class="num">{{ radius }}</span>
            </label>
          </div>
        </div>
      </section>

      <!-- 道具（聚合，不含坐标；原始事件与坐标仅经导出获取） -->
      <section>
        <div v-if="locked(true)" class="card locked-box">
          <div class="lock-icon">🔒</div>
          <p class="lock-title">道具分析属于进阶版功能</p>
          <p class="lock-desc">升级后解锁：投掷物类型/玩家/回合统计与全部道具事件。</p>
        </div>
        <template v-else-if="grenades">
          <div class="stat-grid">
            <div class="card stat-card">
              <h3>按类型</h3>
              <div v-for="t in grenadeByType" :key="t.type" class="stat-row">
                <span>{{ t.label }}</span>
                <span class="num">{{ t.count }}</span>
              </div>
            </div>
            <div class="card stat-card">
              <h3>按玩家</h3>
              <div v-for="p in grenadeByPlayer.slice(0, 10)" :key="p.name" class="stat-row">
                <span class="ellipsis">{{ p.name }}</span>
                <span class="num">{{ p.count }}</span>
              </div>
            </div>
            <div class="card stat-card">
              <h3>按回合</h3>
              <div class="round-bars">
                <div v-for="r in grenadeByRound" :key="r.round" class="round-bar" :title="`回合 ${r.round}：${r.count} 个`">
                  <div class="rb-fill" :style="{ height: Math.max(8, (r.count / Math.max(...grenadeByRound.map((x) => x.count))) * 100) + '%' }"></div>
                  <span class="rb-label num">{{ r.round }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </section>
      </details>

      <!-- 团队（Max-only） -->
      <section v-if="activeTab === 'team'">
        <div v-if="!teamAllowed" class="card locked-box">
          <div class="lock-icon">🔒</div>
          <p class="lock-title">团队分析属于高阶版（Max）功能</p>
          <p class="lock-desc">升级高阶版后解锁：团队汇总、团队同步回放、补枪率等深度配合指标。</p>
        </div>
        <!-- 6R18：团队区块结构化状态——绝不因 403/409/网络失败而静默空白 -->
        <div v-else-if="teamGateStatus === 'loading' || teamGateStatus === 'idle'" class="card team-gate team-gate-loading">
          <p class="team-gate-reason">正在加载团队分析…</p>
        </div>
        <div v-else-if="teamGateStatus === 'roster'" class="card team-gate team-gate-roster">
          <div class="team-gate-head"><span class="team-gate-icon" aria-hidden="true">🤝</span><h3 class="row-title">团队深度分析暂不可用</h3></div>
          <p class="team-gate-reason">{{ teamGateInfo ? teamGateInfo.message : '团队 roster 与本场命中不足，暂时无法生成团队深度报告' }}</p>
          <p v-if="teamGateInfo && teamGateInfo.matched != null" class="team-gate-metric num">
            当前命中 <b>{{ teamGateInfo.matched }}</b> 人 / 要求 <b>{{ teamGateInfo.matchedRequired }}</b> 人
          </p>
          <div class="team-gate-actions">
            <router-link class="btn btn-secondary" to="/profile">管理团队 roster / 绑定 Steam</router-link>
            <button class="btn btn-ghost" @click="retryTeam">重试</button>
          </div>
          <p class="team-gate-note">团队深度报告要求你的团队实名 roster 与本场至少命中 3 名成员，并且你本人在这场比赛里。请先补全 roster，或确认已绑定正确的 Steam。</p>
        </div>
        <div v-else-if="teamGateStatus === 'artifact'" class="card team-gate team-gate-artifact">
          <div class="team-gate-head"><span class="team-gate-icon" aria-hidden="true">⏳</span><h3 class="row-title">该场战术数据尚未就绪</h3></div>
          <p class="team-gate-reason">{{ teamGateInfo ? teamGateInfo.message : '该场战术数据缺失或需重新解析' }}</p>
          <button class="btn btn-secondary" @click="retryTeam">重试重新加载</button>
        </div>
        <div v-else-if="teamGateStatus === 'network'" class="card team-gate team-gate-network">
          <div class="team-gate-head"><span class="team-gate-icon" aria-hidden="true">⚠️</span><h3 class="row-title">网络异常</h3></div>
          <p class="team-gate-reason">{{ teamGateInfo ? teamGateInfo.message : '网络异常，请检查连接后重试' }}</p>
          <button class="btn btn-secondary" @click="retryTeam">重试</button>
        </div>
        <div v-else-if="teamGateStatus === 'error'" class="card team-gate team-gate-error">
          <div class="team-gate-head"><span class="team-gate-icon" aria-hidden="true">⚠️</span><h3 class="row-title">团队分析加载失败</h3></div>
          <p class="team-gate-reason">{{ teamGateInfo ? teamGateInfo.message : '团队分析加载失败，请重试' }}</p>
          <button class="btn btn-secondary" @click="retryTeam">重试</button>
        </div>
        <template v-else-if="teamStats">
          <div class="team-summary">
            <div v-for="t in teamRows" :key="t.team" class="ts-team" :class="t.team === 'CT' ? 'ct' : 't'">
              <span class="ts-name">{{ t.team }}</span>
              <span class="ts-kpis">
                <span class="ts-kpi"><b class="num">{{ t.kills }}</b><span>击杀</span></span>
                <span class="ts-kpi"><b class="num">{{ t.deaths }}</b><span>死亡</span></span>
                <span class="ts-kpi"><b class="num">{{ t.assists }}</b><span>助攻</span></span>
                <span class="ts-kpi"><b class="num">{{ t.team_adr.toFixed(1) }}</b><span>ADR</span></span>
                <span class="ts-kpi"><b class="num">{{ t.first_kills }}</b><span>首杀</span></span>
              </span>
              <span class="ts-count num">{{ t.players }} 人</span>
            </div>
          </div>

          <!-- 6R21F-E6A-MEM/Lifecle：真实产品子切换「团队协作 / 区域控制」，同一时刻只挂载/运行一个回放（消除双 60fps 循环并发） -->
          <div class="e6a-view-switch wb-timeline-switch" role="tablist" aria-label="团队复盘视图">
            <button type="button" class="e6a-vtab" :class="{ on: teamView === 'replay' }" role="tab" :aria-selected="teamView === 'replay'" @click="teamView = 'replay'">团队协作</button>
            <button v-if="isMapControlDemo" type="button" class="e6a-vtab" :class="{ on: teamView === 'mapcontrol' }" role="tab" :aria-selected="teamView === 'mapcontrol'" @click="teamView = 'mapcontrol'">区域控制</button>
          </div>

          <!-- 6R8：团队同步回放（Max 首个主体） -->
          <div class="tr-card" v-if="teamView === 'replay'">
            <div class="tr-head"><h3 class="row-title">团队同步回放</h3></div>
            <TeamSyncReplay v-if="showTeamSync" ref="teamSyncRef" :demo-id="demoId" :map-name="report.match.map_name" :max-round="report.match.rounds" />
          </div>

          <!-- 6R21F-E6D：真实 NavMesh 连续控制场（权威 demo×地图 门控；非接入地图不注入） -->
          <div v-if="teamView === 'mapcontrol' && showMcr && e5n.sample && e5n.nav" class="tr-card" data-testid="mcr-mount" :data-e5n="e5n.map">
            <div class="tr-head"><h3 class="row-title">NavMesh 连续控制场 <span class="row-sub">（{{ mapLabel(e5nMap) }} · {{ e5nMap }} NavMesh 真值）</span></h3></div>
            <MapControlReplay :sample="e5n.sample" :nav="e5n.nav" />
          </div>
          <div v-else-if="teamView === 'mapcontrol' && isMapControlDemo && e5n.err" class="tr-card" data-testid="mcr-mount" data-e5n="unavailable">
            <div class="tr-head"><h3 class="row-title">NavMesh 连续控制场</h3></div>
            <p class="row-sub">当前环境未提供 {{ mapLabel(e5nMap) }} NavMesh/样本（{{ e5n.err }}）。本场为 {{ mapLabel(e5nMap) }}·{{ e5nMap }}，但控制场暂不可用；其余分析不受影响。</p>
          </div>

          <!-- 6R8：深度配合指标（含七项 A/B 表 + 证据）：点击定位到上方回放 -->
          <div class="prim-card">
            <div class="prim-head"><h3 class="row-title">深度配合指标 <span class="row-sub">（机会归一 · 试验性基线）</span></h3></div>
            <CoordinationInsight :demo-id="demoId" :map-name="report.match.map_name" :max-round="report.match.rounds" @locate="locateReplay" />
          </div>

          <!-- 团队交战图（Max 交战面，与个人交战面数据范围隔离） -->
          <div class="tcm-card">
            <div class="tcm-head"><h3 class="row-title">团队交战图</h3></div>
            <TeamCombatMap :demo-id="demoId" :map-name="report.match.map_name" />
          </div>
        </template>
      </section>
      <!-- AI 教练 -->
      <section v-if="activeTab === 'ai'">
        <!-- 锁定态：免费/未授权 -->
        <div v-if="aiLocked" class="locked-box">
          <p class="lock-title">AI 教练 · 进阶版</p>
          <p class="lock-desc">基于本场真实数据生成「问题 + 练习建议」复盘。当前可先在「个人复盘 / 团队复盘」查看可解释的结论与证据，再做针对性练习。</p>
          <p class="lock-hint">月卡 39 元 / 年卡 299 元 —— 支付功能即将开放</p>
        </div>

        <template v-else>
          <p v-if="aiError" class="form-error-box">{{ aiError }}</p>

          <!-- 无报告：诚实预览 + 生成入口 -->
          <div v-if="!aiReport" class="ai-preview">
            <div class="ai-preview-head">
              <h3 class="row-title">AI 教练</h3>
              <span class="ai-tier">进阶版</span>
            </div>
            <p class="ai-preview-desc">
              当前 AI 教练使用<strong>开发测试模板</strong>离线生成本场「优势 / 问题 / 建议 / 证据」结构，<b>不把任何数据发送到外部</b>；
              模板生成的报告会标注为测试 / 过期，<b>不可直接作为正式复盘结论</b>。功能接入真实模型后可提供更可信结论。
              更细的可解释结论可先看：
            </p>
            <div class="ai-preview-guide">
              <span class="ai-guide-lbl">个人复盘</span>
              <button class="btn btn-ghost" @click="activeTab = 'my'">进阶数据分析</button>
              <button class="btn btn-ghost" @click="activeTab = 'team'">团队复盘</button>
            </div>
            <button class="btn btn-primary ai-generate" :disabled="aiGenerating" @click="generateAiReport">
              {{ aiGenerating ? '生成中…' : '开始生成' }}
            </button>
          </div>

          <!-- 生成中 -->
          <div v-else-if="aiGenerating || ['pending', 'running'].includes(aiReport?.status)" class="card ai-progress">
            <div class="bar running"><div class="bar-fill shimmer"></div></div>
            <p class="ai-status">报告生成中…（{{ aiReport.status }}）</p>
          </div>

          <!-- 失败：重试 -->
          <div v-else-if="aiReport.status === 'failed'" class="card ai-failed">
            <p class="ai-err">{{ aiReport.error || '报告生成失败' }}</p>
            <button class="btn btn-primary" @click="retryAiReport">重新生成</button>
          </div>

          <!-- 成功且可信：渲染正文（已消毒） -->
          <div v-else-if="aiReport.status === 'succeeded' && aiTrust.trusted" class="ai-md">
            <div class="ai-head">
              <span class="pill pill-ok">AI 诊断报告</span>
              <span class="ai-meta num">
                {{ aiReport.provider }} / {{ aiReport.model }} ·
                {{ aiReport.prompt_version }} · {{ aiReport.token_in + aiReport.token_out }} tokens · ¥{{ ((aiReport.cost_cents || 0) / 100).toFixed(2) }}
              </span>
              <button class="btn btn-ghost" @click="retryAiReport">重新生成</button>
            </div>
            <div class="md-body" v-html="aiHtml"></div>
          </div>

          <!-- 成功但不可信（测试/过期）：诚实面板，不渲染旧正文/模板键 -->
          <div v-else-if="aiReport.status === 'succeeded'" class="ai-suspect">
            <div class="ai-suspect-head">
              <h3 class="row-title">AI 教练报告暂不可用</h3>
              <span class="pill pill-warn">测试 / 过期</span>
            </div>
            <p class="ai-suspect-desc">
              这份 AI 报告来自<strong>开发测试模板</strong>或<strong>基于已过期的比赛数据</strong>，不能作为本场复盘的正式结论。
              暂不展示其正文；可重新生成，或先从「个人复盘 / 团队复盘」查看可解释结论。
              <template v-if="aiSuspectReasons.length"><span class="ai-suspect-why">原因：{{ aiSuspectReasons.map((r) => AI_TRUST_REASON_CN[r] || r).join('；') }}。</span></template>
            </p>
            <div class="ai-suspect-actions">
              <button class="btn btn-primary" :disabled="aiGenerating" @click="generateAiReport">{{ aiGenerating ? '生成中…' : '重新生成' }}</button>
              <button class="btn btn-secondary" @click="activeTab = 'my'">去个人复盘</button>
              <button class="btn btn-secondary" @click="activeTab = 'team'">去团队复盘</button>
            </div>
            <details class="ai-suspect-tech">
              <summary>技术详情（provider / 版本 / 不可信原因）</summary>
              <div class="ai-suspect-tech-body">
                <div class="ai-tech-row"><span>provider</span><b>{{ aiReport.provider }}</b></div>
                <div class="ai-tech-row"><span>model</span><b>{{ aiReport.model }}</b></div>
                <div class="ai-tech-row"><span>版本</span><b>{{ aiReport.prompt_version }}</b></div>
                <div class="ai-tech-row"><span>status</span><b>{{ aiReport.status }}</b></div>
                <div v-if="aiReport.metrics_summary" class="ai-tech-row"><span>报告基于</span><b class="ai-tech-scope">
                  {{ aiReport.metrics_summary.map_name }} · {{ aiReport.metrics_summary.score_ct }}:{{ aiReport.metrics_summary.score_t }} · {{ aiReport.metrics_summary.rounds }} 回合
                </b></div>
                <div class="ai-tech-row"><span>不可信原因</span><b class="ai-tech-scope">{{ aiSuspectReasons.map((r) => AI_TRUST_REASON_CN[r] || r).join('；') }}</b></div>
              </div>
            </details>
          </div>
        </template>
      </section>
    </template>
  </div>
</template>

<style scoped>
.diag-drawer { margin-top: 14px; }
.diag-drawer summary { cursor: pointer; font-weight: 600; }
.report { max-width: 1160px; }
.head {
  display: flex; align-items: center; gap: 16px; margin-bottom: 20px;
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px 20px;
}
/* 6R21F-F3：工作台上下文条在既有 .head 基础上叠加 .wb-context 布局（.head 的 flex 为 externo，内部用 wb-main/wb-status 分栏） */
.report .wb-context { align-items: center; gap: 20px; }
.report .wb-context .wb-main { flex: 1 1 auto; min-width: 0; }
.report .wb-context .wb-status { flex: 0 0 auto; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; min-width: 0; }
.report .wb-context .wb-score { font-size: 30px; }
.report .wb-context .wb-side .team { color: var(--text-primary); }

.head-main { flex: 1 1 auto; min-width: 0; }
.map-line { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.map-name { font-size: var(--f-h1); font-weight: 700; letter-spacing: .5px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.map-tech { font-size: 11px; font-weight: 500; color: var(--text-muted); white-space: nowrap; flex-shrink: 0; }
.score-row { display: flex; align-items: center; gap: 12px; margin-top: 8px; flex-wrap: wrap; }
.team { font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 42%; }
.team-a { color: var(--info); }
.team-b { color: var(--warn); }
.score { font-size: 28px; font-weight: 800; letter-spacing: 1px; color: var(--text-primary); padding: 0 4px; }
.meta { color: var(--text-secondary); font-size: 13px; margin-top: 8px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.dot { color: var(--text-muted); }
.head-side { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }
.share-btn { min-height: 34px; padding: 6px 12px; }

.id-status { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: normal; max-width: 100%; }
.id-status .id-status-text { min-width: 0; overflow-wrap: anywhere; }
.idb-ok { color: var(--ok); background: var(--ok-soft); }
.idb-warn { color: var(--warn); background: var(--warn-soft); }
.idb-muted { color: var(--text-secondary); background: var(--bg-elevated); }
.idb-link { color: var(--accent); font-weight: 600; font-size: 12px; }
.me-row { background: var(--accent-soft); }
.me-row .pname { color: var(--accent); }
.me-badge { display: inline-block; margin-left: 8px; padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 700; color: var(--accent); background: var(--accent-soft); border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); white-space: nowrap; }

.export-section { margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.export-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.export-label { font-size: var(--f-label); color: var(--text-muted); margin-right: 4px; }

.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); margin-bottom: 22px; }
.tab {
  border: none; background: transparent; color: var(--text-secondary);
  padding: 11px 18px; font-size: 14px; font-weight: 600; cursor: pointer;
  border-bottom: 2px solid transparent; white-space: nowrap;
}
.tab:hover { color: var(--text-primary); }
.tab.on { color: var(--accent); border-bottom-color: var(--accent); }

.table-card { padding: 8px 14px 12px; }
.conclusion {
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  margin-bottom: 14px; padding: 16px 20px;
}
.concl-main { min-width: 0; }
.concl-eyebrow { font-size: 12px; font-weight: 700; letter-spacing: .12em; color: var(--text-muted); text-transform: uppercase; }
.concl-score { display: flex; align-items: baseline; gap: 10px; margin-top: 8px; }
.concl-num { font-size: 30px; font-weight: 800; }
.concl-colon { color: var(--text-muted); font-size: 22px; }
.concl-desc { color: var(--text-secondary); font-size: 13px; margin-top: 8px; }
.concl-actions { display: flex; gap: 10px; flex-wrap: wrap; flex-shrink: 0; }
.table-scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.tbl th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 10px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.tbl td { padding: 8px 10px; border-bottom: 1px solid rgba(35, 45, 61, 0.5); white-space: nowrap; }
.tbl tr:last-child td { border-bottom: none; }
.pname { font-weight: 600; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
.victim { color: var(--text-secondary); font-weight: 400; }
.muted { color: var(--text-muted); }
.adr { color: var(--accent); font-weight: 700; }
.weapon { background: var(--bg-elevated); padding: 1px 8px; border-radius: 4px; font-size: 12.5px; }
.tbl-note { color: var(--text-muted); font-size: 12px; margin-top: 8px; }

.team-tag { display: inline-block; padding: 1px 8px; border-radius: 4px; font-size: 11.5px; font-weight: 700; }
.team-tag.ct { background: var(--info-soft); color: var(--info); }
.team-tag.t { background: var(--warn-soft); color: var(--warn); }

.hm-card { display: flex; flex-direction: column; align-items: center; }
.hm-modes { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-bottom: 14px; }
.mode-btn {
  border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary);
  padding: 6px 14px; border-radius: 999px; font-size: 13px; cursor: pointer; font-weight: 600;
}
.mode-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.mode-btn.locked { opacity: 0.75; }
.lock-tag { font-size: 10px; background: var(--accent); color: #14100a; padding: 1px 6px; border-radius: 4px; margin-left: 4px; vertical-align: 1px; }

.controls { display: flex; gap: 28px; margin-top: 14px; flex-wrap: wrap; justify-content: center; }
.ctl { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--text-secondary); }
.ctl input[type='range'] { accent-color: var(--accent); }

.locked-box { text-align: left; padding: 18px 16px; display: flex; flex-direction: column; gap: 8px; background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius); }
.lock-title { font-weight: 700; font-size: 15px; }
.lock-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.lock-hint { color: var(--text-muted); font-size: 12px; }

.stat-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
.stat-card h3 { font-size: 14px; margin-bottom: 10px; color: var(--text-secondary); }
.stat-row { display: flex; justify-content: space-between; padding: 5px 0; font-size: 13px; border-bottom: 1px dashed rgba(35,45,61,.4); }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px; }
.round-bars { display: flex; align-items: flex-end; gap: 6px; height: 120px; }
.round-bar { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 4px; height: 100%; }
.rb-fill { width: 70%; background: linear-gradient(180deg, var(--accent), var(--accent-soft)); border-radius: 3px 3px 0 0; }
.rb-label { font-size: 11px; color: var(--text-muted); }

.mt { margin-top: 14px; }
.sub-h { font-size: 14px; margin: 10px 0 6px; color: var(--text-secondary); }

/* 回合走势 */
.round-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.round-row { display: flex; align-items: center; gap: 10px; }
.round-no { width: 40px; font-size: 12.5px; color: var(--text-muted); }
.round-bar-track { flex: 1; height: 14px; background: var(--bg-elevated); border-radius: 4px; overflow: hidden; }
.round-bar-fill { height: 100%; background: linear-gradient(90deg, var(--accent-soft), var(--accent)); border-radius: 4px; transition: width .3s; }
.round-cnt { width: 28px; text-align: right; font-size: 12.5px; }

/* 团队：紧凑两队摘要条（非大卡） */
.team-summary { display: flex; flex-direction: column; gap: 0; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; margin-bottom: 16px; }
.ts-team { display: flex; align-items: center; gap: 14px; padding: 12px 16px; flex-wrap: wrap; }
.ts-team.ct { border-left: 3px solid var(--info); }
.ts-team.t { border-left: 3px solid var(--warn); }
.ts-team + .ts-team { border-top: 1px solid var(--border); }
.ts-name { font-size: 16px; font-weight: 800; letter-spacing: 1px; }
.ts-team.ct .ts-name { color: var(--info); }
.ts-team.t .ts-name { color: var(--warn); }
.ts-kpis { display: flex; gap: 14px; flex-wrap: wrap; margin-left: auto; }
.ts-kpi { display: inline-flex; align-items: baseline; gap: 4px; color: var(--text-muted); font-size: 12px; }
.ts-kpi b { font-size: 17px; color: var(--text-primary); font-weight: 700; }
.ts-count { color: var(--text-muted); font-size: 12.5px; }

/* 团队各段（新主体，去大卡围栏：用分割与留白） */
.row-title { font-size: 15px; font-weight: 700; color: var(--text-primary); margin: 0; letter-spacing: .3px; }
.row-sub { font-size: 12px; color: var(--text-muted); font-weight: 500; }
.tr-card { border-top: 1px solid var(--border); padding-top: 16px; margin-top: 16px; }
.e6a-view-switch { display: flex; gap: 6px; margin: 6px 0 4px; }
.e6a-vtab { min-height: 44px; min-width: 44px; padding: 0 14px; border: 1px solid var(--border-strong); background: #0d1218; color: #aab4c2; font-size: 12px; cursor: pointer; }
.e6a-vtab.on { color: #f0a020; border-color: #f0a020; background: #0d1218; }
.e6a-vtab:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tr-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.prim-card { border-top: 1px solid var(--border); padding-top: 16px; margin-top: 16px; }
.prim-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.tcm-card { border-top: 1px solid var(--border); padding-top: 16px; margin-top: 16px; }
.tcm-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.todo-card h3 { font-size: 14px; margin-bottom: 8px; color: var(--text-secondary); }
.todo-list { list-style: none; }
.todo-list li { display: flex; justify-content: space-between; padding: 7px 0; font-size: 13px; border-bottom: 1px dashed rgba(35,45,61,.4); }
.todo-name { color: var(--text-primary); }
.todo-state { color: var(--text-muted); font-size: 12px; }

/* AI 诊断：克制预览态（非通用聊天框） */
.ai-preview { padding: 4px 2px; display: flex; flex-direction: column; gap: 12px; border-top: 1px solid var(--border); }
.ai-preview-head { display: flex; align-items: center; gap: 10px; }
.ai-preview-head .row-title { margin: 0; }
.ai-tier { font-size: 12px; color: var(--text-muted); font-weight: 600; }
.ai-preview-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; max-width: 76ch; margin: 0; }
.ai-preview-desc b { color: var(--text-primary); font-weight: 600; }
.ai-preview-guide { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ai-guide-lbl { color: var(--text-muted); font-size: 12.5px; }
.ai-preview-guide .btn { min-height: 34px; padding: 6px 12px; }
.ai-generate { align-self: flex-start; margin-top: 4px; }
.ai-suspect { padding: 4px 2px; display: flex; flex-direction: column; gap: 12px; border-top: 1px solid var(--border); }
.ai-suspect-head { display: flex; align-items: center; gap: 10px; }
.ai-suspect-head .row-title { margin: 0; }
.ai-suspect-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; max-width: 76ch; margin: 0; }
.ai-suspect-desc strong { color: var(--warn); font-weight: 600; }
.ai-suspect-why { display: block; margin-top: 6px; color: var(--text-muted); font-size: 12.5px; }
.ai-suspect-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.ai-suspect-actions .btn { min-height: 36px; }
.ai-suspect-tech { border-top: 1px dashed var(--border); padding-top: 10px; }
.ai-suspect-tech summary { cursor: pointer; font-size: 13px; color: var(--accent); }
.ai-suspect-tech-body { margin-top: 10px; display: grid; gap: 8px; }
.ai-tech-row { display: flex; gap: 10px; font-size: 12px; color: var(--text-secondary); align-items: baseline; }
.ai-tech-row span { min-width: 72px; color: var(--text-muted); }
.ai-tech-row b { color: var(--text-primary); font-weight: 600; }
.ai-tech-scope { font-weight: 400 !important; }
.ai-progress { text-align: center; padding: 32px; }
.ai-status { margin-top: 12px; color: var(--text-secondary); font-size: 13px; }
.ai-failed { text-align: center; padding: 32px; }
.ai-err { color: var(--err); font-size: 13px; margin-bottom: 14px; }
.ai-md { padding: 20px 24px; }
.ai-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; border-bottom: 1px solid var(--border); padding-bottom: 12px; }
.ai-meta { color: var(--text-muted); font-size: 12px; }
.md-body { margin-top: 16px; line-height: 1.8; font-size: 14px; }
.md-body h2 { font-size: 16px; margin: 18px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--border); }
.md-body h2:first-child { margin-top: 0; }
.md-body li { margin: 4px 0; }
.md-body code { background: var(--bg-elevated); padding: 1px 6px; border-radius: 4px; font-size: 12.5px; }

.share-backdrop { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; padding: 16px; background: rgba(0, 0, 0, .68); }
.share-dialog { width: min(620px, 100%); padding: 22px; display: grid; gap: 14px; }
.share-title-row { display: flex; justify-content: space-between; align-items: center; }
.share-title-row h3 { margin: 0; font-size: 19px; }
.share-close { width: 32px; height: 32px; border: 0; border-radius: 6px; background: var(--bg-elevated); color: var(--text-secondary); font-size: 23px; cursor: pointer; }
.share-note, .share-existing, .share-expiry { margin: 0; color: var(--text-secondary); font-size: 13px; line-height: 1.65; }
.share-existing { color: var(--warn); padding: 9px 11px; border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent); border-radius: 7px; background: var(--warn-soft); }
.share-label { display: grid; gap: 6px; color: var(--text-secondary); font-size: 13px; }
.share-link-row { display: flex; gap: 8px; }
.share-link { min-width: 0; flex: 1; font-family: ui-monospace, monospace; font-size: 12px; }
.share-actions { display: flex; gap: 8px; flex-wrap: wrap; }

/* 6R17 概览：结论→观察→证据 */
.conclusion-strip { margin-bottom: 18px; }
.key-observations { margin-top: 18px; }
/* 6R17.2：本场观察——行式编辑要点（首条 lead），非等权卡片墙 */
.obs-list { list-style: none; display: flex; flex-direction: column; gap: 0; margin: 0; padding: 0; }
.obs-line { display: flex; align-items: baseline; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13.5px; min-width: 0; }
.obs-line:last-child { border-bottom: none; }
.obs-line.lead .obs-text { font-weight: 700; font-size: 14.5px; color: var(--text-primary); }
.obs-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; align-self: center; background: var(--text-muted); }
.obs-text { min-width: 0; }
.obs-ok .obs-dot { background: var(--ok); }
.obs-accent .obs-dot { background: var(--accent); }
.obs-info .obs-dot { background: var(--info); }
.obs-t .obs-dot { background: var(--warn); }

/* 6R18：团队区块结构化门禁状态（roster 不足 / artifact 缺失 / 网络失败 —— 绝不静默空白） */
.team-gate { padding: 20px 22px; }
.team-gate-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.team-gate-icon { font-size: 20px; line-height: 1; }
.team-gate-head .row-title { margin: 0; }
.team-gate-reason { color: var(--text-secondary); font-size: 14px; margin: 4px 0 10px; }
.team-gate-metric { color: var(--text-secondary); font-size: 14px; margin: 0 0 12px; }
.team-gate-metric b { color: var(--text-primary); }
.team-gate-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.team-gate-note { color: var(--text-muted); font-size: 12.5px; margin: 12px 0 0; line-height: 1.5; }
.team-gate-roster { border-left: 3px solid var(--warn); }
.team-gate-artifact { border-left: 3px solid var(--info); }
.team-gate-network { border-left: 3px solid var(--danger, #f85149); }
.team-gate-error { border-left: 3px solid var(--danger, #f85149); }
.team-gate-loading { color: var(--text-secondary); font-size: 14px; }

@media (max-width: 720px) {
  .stat-grid { grid-template-columns: 1fr; }
  .head { flex-direction: column; align-items: flex-start; padding: 12px 14px; }
  .head-side { width: 100%; justify-content: flex-start; }
  .team { max-width: none; }
  .share-link-row { flex-direction: column; }
  .tabs { overflow-x: auto; overflow-y: hidden; -webkit-overflow-scrolling: touch; }
  /* 6R21F-F3：移动端上下文条保持紧凑（避免把团队回放推出首屏）；权益/分享横向内联 */
  .report .wb-context { gap: 6px; padding: 10px 14px; }
  .report .wb-context .wb-main { width: 100%; max-width: 100%; min-width: 0; }
  .report .wb-context .wb-map .map-tech { display: none; }
  .report .wb-context .wb-score-row { margin-top: 3px; gap: 8px; min-width: 0; flex-wrap: nowrap; }
  .report .wb-context .wb-side { min-width: 0; }
  .report .wb-context .wb-side .team { max-width: 38vw; font-size: 13px; }
  .report .wb-context .wb-score { font-size: 22px; }
  .report .wb-context .wb-meta { margin-top: 4px; gap: 6px; flex-wrap: nowrap; white-space: nowrap; min-width: 0; }
  .report .wb-context .wb-meta .id-status { font-size: 11px; padding: 1px 7px; white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; display: inline-block; flex: 0 1 auto; min-width: 16px; vertical-align: bottom; }
  .report .wb-context .wb-status { flex-direction: row; align-items: center; flex-wrap: wrap; gap: 8px; }
  .report .wb-context .wb-side-actions { justify-content: flex-start; }
  /* 6R17.2：移动端两队摘要紧凑（保留可见性，压缩 kpi，地图/回放仍靠前） */
  .ts-team { padding: 8px 12px; gap: 8px; }
  .ts-kpis .ts-kpi:nth-child(2), .ts-kpis .ts-kpi:nth-child(3), .ts-kpis .ts-kpi:nth-child(5) { display: none; }
  .ts-count { display: none; }
  .team-gate { padding: 16px 14px; }
  .team-gate-actions .btn { flex: 1 1 auto; }
}
</style>
