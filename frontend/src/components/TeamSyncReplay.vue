<script setup>
/**
 * 6R8：团队十人同步回放（Max 主视图）—— TeamReplay 的升级版。
 * - 数据源：/api/demos/{id}/team/replay?round_no=N（单回合全部玩家 + 真实 yaw/pitch + 道具生命周期）
 * - 时间原点 = round.freeze_end_tick（00:00），可播放窗口 [freeze_end_tick, end_tick]
 * - 视角朝向：方向 = normalize(cos yaw, -sin yaw)（雷达屏幕方向；世界前向 = (cos yaw, sin yaw)，
 *   yaw 插值，±180 最短角路径绕回）。6R12 已修正旧式 90° 错误变换（-sin, -cos）。
 * - 道具生命周期：烟/火仅在 [active_start_sec, active_end_sec] 出现；HE/flash 为 ~0.6s 瞬时反馈
 * - 独立图层开关（默认开）：玩家名 / 路线 / 真实朝向·枪线 / 烟 / 火 / 闪 / 手雷（投掷轨迹可选）
 * - 诚实性：主界面只给 秒(mm:ss) + 中文区域；bare tick/世界坐标/yaw/pitch 只在「技术诊断」折叠区
 * - 测试钩子：canvas 上 data-ready / data-gun / data-smoke / data-fire / data-time
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import { layoutLabels } from '../utils/labelLayout'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
  maxRound: { type: Number, default: 0 },
})

const SIZE = 720
const TEAM_COLOR = { A: '#58a6ff', B: '#f0a020' }   // 稳定队伍色（与 TeamReplay 一致）
const MARK = {
  spawn: '#3fb950', death: '#f85149', end: '#3fb950', ball: '#ffffff',
  focusRing: '#ffffff', kill: '#f5c518',
}
const PROP_COLOR = {
  smoke: 'rgba(150,170,190,0.45)',
  fire: 'rgba(240,120,40,0.5)',
  he: 'rgba(240,200,60,0.9)',
  flash: 'rgba(255,255,255,0.85)',
  trajectory: 'rgba(125,140,158,0.5)',
}
const ROUTE_ALPHA = 0.62
const FADED_ALPHA = 0.14
const GUNLEN = 48            // 枪线长度（720 尺寸下）
const GUN_HALF = 12.5        // 枪线扇形半角（°）→ 总 25°
const FLASH_SEC = 0.6        // HE/flash 瞬时反馈时长
const SHOOT_LEN = 62         // 瞬时"射击线"长度
const SHOOT_WINDOW = 0.45    // 射击线可见时间窗（秒，相对击杀秒）
const KILLFEED_SEC = 4.0     // 击杀播报可见时长（秒，自然淡出）
const HE_HIT_SEC = 1.0       // HE 真实受伤高亮时长（秒）
const MAX_KILLFEED_ROWS = 6  // 同屏击杀播报最大堆叠行（同刻多杀堆叠，不横滚）
// 致盲效果颜色（自闪/队闪/敌闪 语义分离，绝不与烟/火/HE/枪线共用含混标识）
const BLIND_COLOR = { self: 'rgba(255,255,255,0.9)', team: 'rgba(90,166,255,0.9)', enemy: 'rgba(248,81,73,0.9)' }
const BLIND_LABEL = { self: '自闪', team: '队闪', enemy: '敌闪' }

// 区域控制状态颜色（CT/T 分色 + 争夺第三状态；"占据≠控制"在图例中说明）
const AREA_STATE_COLOR = {
  // A=CT 蓝、B=T 橙（与队伍队色一致）
  occupiedA: 'rgba(88,166,255,0.20)', controlledA: 'rgba(88,166,255,0.42)',
  occupiedB: 'rgba(240,160,32,0.20)', controlledB: 'rgba(240,160,32,0.42)',
  influenceA: 'rgba(88,166,255,0.10)', influenceB: 'rgba(240,160,32,0.10)',
  contested: 'rgba(226,165,74,0.40)',   // 争夺：明确第三状态（琥珀）
}
const AREA_STROKE = {
  occupiedA: 'rgba(88,166,255,0.85)', controlledA: 'rgba(88,166,255,1)',
  occupiedB: 'rgba(240,160,32,0.85)', controlledB: 'rgba(240,160,32,1)',
  influenceA: 'rgba(88,166,255,0.45)', influenceB: 'rgba(240,160,32,0.45)',
  contested: 'rgba(226,165,74,0.95)',
}
const AREA_STATE_CN = {
  occupied: '占据', controlled: '控制', influence: '影响', contested: '争夺', unknown: '未知',
}

const canvasRef = ref(null)
const radarImg = ref(null)
const team = ref(null)
const loading = ref(false)
const error = ref('')
const playing = ref(false)
const currentTime = ref(0)
const filter = ref('all')       // all | A | B
const highlight = ref(null)     // player_id or null
const selectedRound = ref(1)
const speed = ref(1)
let pendingLocate = null
let rafId = 0, lastTs = 0, loadGeneration = 0

// 图层开关（默认开）
const toggles = reactive({
  playerName: true, route: true, gun: true,
  smoke: true, fire: true, flash: true, he: true, trajectory: false,
  killfeed: true, blind: true, area: true,
})

const reducedMotion = ref(false)
function detectReducedMotion() {
  reducedMotion.value = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches : false
}
detectReducedMotion()

const round = computed(() => team.value?.round || {})
const map = computed(() => team.value?.map || {})
const players = computed(() => team.value?.players || [])
const propItems = computed(() => team.value?.props || [])
const shootItems = computed(() => team.value?.shoots || [])
const killfeedItems = computed(() => team.value?.killfeed || [])
const blindItems = computed(() => team.value?.blinds || [])
const heDamageItems = computed(() => team.value?.he_damage || [])
const areaStateMachine = computed(() => team.value?.area_state_machine || [])
const regionGeometry = computed(() => team.value?.region_geometry || {})
const maxDuration = computed(() => team.value?.max_duration || 0)
const focusPlayerId = computed(() => team.value?.focus_player_id)
const winner = computed(() => team.value?.winner)
// 只信任后端提供的 tick_rate：为正数才核算秒数；缺失/为 0/不可证明时绝不伪造，标记为「时长不可核算」。
const tickRate = computed(() => {
  const r = Number(team.value?.tick_rate)
  return Number.isFinite(r) && r > 0 ? r : null
})
const tickRateValid = computed(() => tickRate.value != null)
// 时间原点：freeze_end_tick（00:00），绝不从 start_tick 起算。
const freezeTick = computed(() => round.value.freeze_end_tick ?? round.value.start_tick ?? 0)

const summary = computed(() => {
  const ps = players.value
  const a = ps.filter((p) => p.team_identity === 'A')
  const b = ps.filter((p) => p.team_identity === 'B')
  const alive = ps.filter((p) => p.outcome === 'alive').length
  return { a: a.length, b: b.length, alive }
})

function fmtTime(sec) {
  if (!Number.isFinite(sec)) return '—'
  const s = Math.max(0, sec); const m = Math.floor(s / 60); const r = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

// 道具有效区间文本（技术诊断用，仅在无证明时诚实标注）
function propWindowText(pr) {
  const s = pr.active_start_sec, e = pr.active_end_sec
  const sn = s == null || s === '' ? NaN : Number(s)
  const en = e == null || e === '' ? NaN : Number(e)
  if (!Number.isFinite(sn)) return '未证明'
  if (pr.type === 'he' || pr.type === 'flash') return `@${sn.toFixed(2)}s`
  if (Number.isFinite(en)) return `${sn.toFixed(2)}s–${en.toFixed(2)}s`
  return '未证明'
}

function drawDot(ctx, rx, ry, r, fill, stroke) { ctx.beginPath(); ctx.arc(rx * SIZE, ry * SIZE, r, 0, Math.PI * 2); ctx.fillStyle = fill; ctx.fill(); if (stroke) { ctx.lineWidth = 2; ctx.strokeStyle = stroke; ctx.stroke() } }
function drawX(ctx, rx, ry, size, color) { ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineCap = 'round'; const x = rx * SIZE, y = ry * SIZE; ctx.beginPath(); ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size); ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size); ctx.stroke() }
function drawDiamond(ctx, rx, ry, size, color) { const x = rx * SIZE, y = ry * SIZE; ctx.beginPath(); ctx.moveTo(x, y - size); ctx.lineTo(x + size, y); ctx.lineTo(x, y + size); ctx.lineTo(x - size, y); ctx.closePath(); ctx.fillStyle = color; ctx.fill() }
function drawPath(ctx, pts, color, width, alpha, dash) { if (pts.length < 2) return; ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; if (dash) ctx.setLineDash(dash); ctx.beginPath(); ctx.moveTo(pts[0].rx * SIZE, pts[0].ry * SIZE); for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].rx * SIZE, pts[i].ry * SIZE); ctx.stroke(); ctx.restore() }

// ---- 6R21C 确定性二维标签布局/避让 ----
// 玩家短名 + 区域状态标签统一收集为一组锚点，交给 layoutLabels 做确定性二维布局：
//   盒-盒不重叠、盒不覆盖其它锚点（玩家球/区域中心）、盒避开击杀播报/图例面板（blocked）、
//   盒始终在画布内；必要时用短引线、左右/上下翻转、优先级、可读折叠。
// 纯函数布局 → 同一 seek 时刻两次重建输出完全一致（不破坏 data-area-hash / canvasChecksum 确定性）。
let _labelAnchors = []   // 本帧收集的标签锚点
let _labelBlocked = []   // 本帧被占用/面板矩形（击杀播报、图例）
let _killfeedRect = null
let _legendRect = null

// 收集一个标签锚点（ax,ay 为像素中心；text 在 drawLabels 时按队色/白绘制）
function collectLabel(a) {
  const ctx = canvasRef.value.getContext('2d')
  ctx.font = a.font || 'bold 11px system-ui, sans-serif'
  const w = Math.round(ctx.measureText(a.text).width) + 6
  const h = 16
  _labelAnchors.push({
    id: a.id, ax: Math.round(a.ax), ay: Math.round(a.ay), w, h,
    priority: a.priority ?? 40, text: a.text, color: a.color, font: a.font || 'bold 11px system-ui, sans-serif',
  })
}

// 统一布局并绘制全部标签：先 layout（确定性），再画短引线（锚点不在盒内时）+ 背板 + 文本。
function drawLabels(ctx) {
  if (!_labelAnchors.length) return
  const rect = { x: 6, y: 6, w: SIZE - 12, h: SIZE - 12 }
  const placed = layoutLabels(_labelAnchors, { rect, gap: 6, step: 12, blocked: _labelBlocked })
  // 测试钩子：确定性布局 box 列表 + 哈希（供 E2E 断言同一时刻重建一致、盒不重叠、不越界）
  const boxes = []
  let hash = 0
  for (const a of _labelAnchors) {
    const o = placed.get(a.id)
    if (!o) continue
    boxes.push({ id: a.id, x: o.bx, y: o.by, w: a.w, h: a.h, leader: !!o.leader })
    const s = `${o.bx},${o.by},${a.w},${a.h}`
    for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) % 1000000007
  }
  canvasRef.value.setAttribute('data-label-boxes', JSON.stringify(boxes))
  canvasRef.value.setAttribute('data-label-hash', String(hash))

  for (const a of _labelAnchors) {
    const o = placed.get(a.id)
    if (!o) continue
    ctx.save()
    // 短引线（锚点不在盒内时，从锚点指向盒最近边缘，避免歧义归属）
    if (o.leader) {
      const nx = Math.max(o.bx, Math.min(a.ax, o.bx + a.w))
      const ny = Math.max(o.by, Math.min(a.ay, o.by + a.h))
      ctx.strokeStyle = a.color; ctx.lineWidth = 1.5; ctx.globalAlpha = 0.85
      ctx.beginPath(); ctx.moveTo(a.ax, a.ay); ctx.lineTo(nx, ny); ctx.stroke()
    }
    ctx.globalAlpha = 1
    ctx.fillStyle = 'rgba(11,15,20,0.85)'
    ctx.fillRect(o.bx, o.by, a.w, a.h)
    ctx.globalAlpha = 0.5; ctx.strokeStyle = a.color; ctx.lineWidth = 1
    ctx.strokeRect(o.bx + 0.5, o.by + 0.5, a.w - 1, a.h - 1)
    ctx.globalAlpha = 1
    ctx.fillStyle = a.color
    ctx.font = a.font; ctx.textBaseline = 'middle'; ctx.textAlign = 'left'
    ctx.fillText(a.text, o.bx + 3, o.by + a.h / 2)
    ctx.restore()
  }
}

// 最短角路径插值（±180° 绕回）
function lerpYaw(a, b, f) {
  if (a == null && b == null) return null
  if (a == null) return b
  if (b == null) return a
  let diff = b - a
  while (diff > 180) diff -= 360
  while (diff < -180) diff += 360
  return a + diff * f
}

// 当前时刻某玩家的插值状态：位置 + yaw。时间原点为 freeze_end_tick。points 按 tick 有序 → 二分定位。
function playerState(player, time) {
  const pts = player.points
  if (!pts.length || !tickRateValid.value) return { px: null, py: null, yaw: null }
  const target = freezeTick.value + time * tickRate.value
  if (target <= pts[0].tick) return { px: pts[0].rx, py: pts[0].ry, yaw: pts[0].yaw }
  const last = pts[pts.length - 1]
  if (target >= last.tick) return { px: last.rx, py: last.ry, yaw: last.yaw }
  let lo = 0, hi = pts.length - 1
  while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid].tick < target) lo = mid + 1; else hi = mid }
  const b = pts[lo]
  const a = pts[Math.max(0, lo - 1)]
  if (target >= a.tick && target <= b.tick && a.tick !== b.tick) {
    const f = (target - a.tick) / (b.tick - a.tick)
    return { px: a.rx + (b.rx - a.rx) * f, py: a.ry + (b.ry - a.ry) * f, yaw: lerpYaw(a.yaw, b.yaw, f) }
  }
  return { px: b.rx, py: b.ry, yaw: b.yaw }
}

// 死亡 tick：首个 alive===false 的采样点；无显式 false 但 outcome=dead 时取末点。数据驱动，不伪造。
function playerDeathTick(player) {
  const pts = player.points
  if (!pts.length) return null
  if (player.outcome === 'alive') return null
  for (const p of pts) if (p.alive === false) return p.tick
  return pts[pts.length - 1].tick
}

function isLiveAt(player, cTick) {
  const dt = deathTickOf(player)
  if (dt != null) return cTick < dt
  return true // 存活或未知：仍视为在场
}

// ---- 6R21F-F3P：每回合一次性预计算派生数据（不改变真值 points/alive/tick），消除每帧的重复线性扫描与数组分配。
// 数据源仍是真实的 player.points（只读），缓存只存「派生索引」，绝不改写/隐藏任何点。
let _deathTickCache = new Map()        // player_id → death tick（首个 alive===false 的 tick；outcome=alive 为 null）
let _shownCache = null                 // 按 filter 预过滤后的 players 引用（filter/players 不变时复用，避免每帧 filter 分配）
let _shownCacheKey = ''
let _geomByKeyCache = {}               // 每回合预计算的区域几何 key→region（避免 drawAreas 每帧重建对象）

// 路径简化（Ram–Douglas–Peucker，投影到 720×720 像素空间），误差上限 maxErrorPx（默认 2px）。
// 只简化「绘制用的路线折线」，当前球/死亡 X/插值位置仍用原始 points 精确计算；简化点保留原 tick，
// 便于按时间二分「已探索」区间。返回 [{tick, rx, ry}]（首尾必保；区间点误差 <= maxErrorPx）。
const ROUTE_SIM_EPS_PX = 2
function _rdpDist(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return Math.hypot(px - x1, py - y1)
  let tt = ((px - x1) * dx + (py - y1) * dy) / len2
  tt = tt < 0 ? 0 : tt > 1 ? 1 : tt
  return Math.hypot(px - (x1 + tt * dx), py - (y1 + tt * dy))
}
function _rdp(pts, lo, hi, eps, out) {
  if (hi <= lo + 1) return
  let maxD = 0, idx = -1
  const x1 = pts[lo].rx * SIZE, y1 = pts[lo].ry * SIZE
  const x2 = pts[hi].rx * SIZE, y2 = pts[hi].ry * SIZE
  for (let i = lo + 1; i < hi; i++) {
    const d = _rdpDist(pts[i].rx * SIZE, pts[i].ry * SIZE, x1, y1, x2, y2)
    if (d > maxD) { maxD = d; idx = i }
  }
  if (maxD > eps && idx > 0) {
    _rdp(pts, lo, idx, eps, out)
    out.push({ tick: pts[idx].tick, rx: pts[idx].rx, ry: pts[idx].ry })
    _rdp(pts, idx, hi, eps, out)
  }
}
function simplifyRoute(pts, epsPx) {
  if (!pts || pts.length <= 2) return pts
  const out = [{ tick: pts[0].tick, rx: pts[0].rx, ry: pts[0].ry }]
  _rdp(pts, 0, pts.length - 1, epsPx, out)
  out.push({ tick: pts[pts.length - 1].tick, rx: pts[pts.length - 1].rx, ry: pts[pts.length - 1].ry })
  return out
}

// 预计算：build 时一次线性扫描，把每个玩家的死亡 tick + 简化路线缓存起来（数据真值不变，误差上限为 ROUTE_SIM_EPS_PX）。
function buildRoundIndexes() {
  const m = new Map()
  for (const p of players.value) {
    p.__sim = null
    const pts = p.points
    // 死亡 tick（与 playerDeathTick 语义一致）
    let dt = null
    if (pts && pts.length) {
      if (p.outcome !== 'alive') {
        for (const pt of pts) if (pt.alive === false) { dt = pt.tick; break }
        if (dt == null) dt = pts[pts.length - 1].tick
      }
    }
    m.set(p.player_id, dt)
    // 路线简化（仅在点数多时才简化，点数少时原样以保持手头精确；误差上限 <=2px）
    if (pts && pts.length > 64) p.__sim = simplifyRoute(pts, ROUTE_SIM_EPS_PX)
  }
  _deathTickCache = m
}

// O(log N) 求「已探索」末索引：points 按 tick 有序，返回最后一个 tick<=cTick 的下标（无则 -1）。
function exploredEndIndex(pts, cTick) {
  const n = pts.length
  if (!n) return -1
  if (cTick < pts[0].tick) return -1
  if (cTick >= pts[n - 1].tick) return n - 1
  let lo = 0, hi = n - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (pts[mid].tick <= cTick) lo = mid
    else hi = mid - 1
  }
  return lo
}

function deathTickOf(player) {
  const dt = _deathTickCache.get(player.player_id)
  return dt == null ? null : dt
}

// 渲染前按 filter 预过滤（仅当 filter/players 变化时重建，避免每帧 .filter 分配新数组）。
function shownPlayers() {
  const key = filter.value + '|' + (players.value.length) + '|' + (players.value[0]?.player_id || '')
  if (_shownCache && _shownCacheKey === key && _shownCache.length === players.value.length) return _shownCache
  _shownCacheKey = key
  _shownCache = players.value.filter((p) => filter.value === 'all' || p.team_identity === filter.value)
  return _shownCache
}

// 当前区域：焦点玩家 > 突出玩家 > 各可见玩家区域一致时显示；否则省略（诚实）
const currentPlace = computed(() => {
  if (!tickRateValid.value) return null
  const ps = players.value
  if (!ps.length) return null
  const target = focusPlayerId.value != null ? ps.find((p) => p.player_id === focusPlayerId.value)
    : (highlight.value != null ? ps.find((p) => p.player_id === highlight.value) : null)
  const tick = freezeTick.value + currentTime.value * tickRate.value
  const placeOf = (p) => {
    const pts = p.points
    if (!pts?.length) return null
    let lo = 0, hi = pts.length - 1
    while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid].tick < tick) lo = mid + 1; else hi = mid }
    const cand = [pts[Math.min(lo, pts.length - 1)], pts[Math.max(0, lo - 1)]]
    let best = cand[0]; let bestD = Infinity
    for (const pt of cand) { const d = Math.abs((pt.tick ?? 0) - tick); if (d < bestD) { bestD = d; best = pt } }
    return best.place || '未知区域'
  }
  if (target) return placeOf(target)
  if (focusPlayerId.value == null && highlight.value == null) {
    const shown = ps.filter((p) => filter.value === 'all' || p.team_identity === filter.value)
    const places = new Set()
    for (const p of shown) { const pl = placeOf(p); if (pl) places.add(pl) }
    if (places.size === 1) return [...places][0]
    return null
  }
  return null
})

function drawGunFan(ctx, px, py, yaw, color) {
  // 6R12 修正：真实世界前向 = (cos yaw, sin yaw)；雷达/屏幕方向（y 向下）= (cos yaw, -sin yaw)。
  // 旧式 (-sin, -cos)（=世界 (-sin yaw, cos yaw)）经 golden set 实测为 90° 错误变换，勿再使用。
  const rad = yaw * Math.PI / 180
  const theta = Math.atan2(-Math.sin(rad), Math.cos(rad))
  const cx = px * SIZE, cy = py * SIZE
  const half = GUN_HALF * Math.PI / 180
  ctx.save()
  ctx.fillStyle = color
  ctx.globalAlpha = 0.22
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, GUNLEN, theta - half, theta + half); ctx.closePath(); ctx.fill()
  ctx.globalAlpha = 0.9
  ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.lineCap = 'round'
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(theta) * GUNLEN, cy + Math.sin(theta) * GUNLEN); ctx.stroke()
  ctx.restore()
}

// 瞬时"射击线"：仅在 validated + 时间窗内渲染（无权威/方向不可用 → 绝不渲染）
function drawShootLine(ctx, s, color) {
  const cx = s.rx * SIZE, cy = s.ry * SIZE
  const dx = s.dir_x, dy = s.dir_y
  const ex = cx + dx * SHOOT_LEN, ey = cy + dy * SHOOT_LEN
  ctx.save()
  ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineCap = 'round'
  ctx.globalAlpha = 0.95
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey); ctx.stroke()
  // 终点标记
  ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(ex, ey, 4, 0, Math.PI * 2); ctx.stroke()
  ctx.restore()
}

// 玩家 id → 颜色（按队伍身份 A/B 稳定队色，未知名归灰色）
function teamColorOf(team) { return TEAM_COLOR[team] || '#9aa7b8' }

// 玩家在 time 时刻的插值位置（用于致盲/HE 高亮绑定到受害者当前球位置）
function positionOf(player, time) {
  if (!player) return null
  const s = playerState(player, time)
  if (s.px == null || s.py == null) return null
  return s
}

// ---- 6R13 击杀播报：在真实事件时刻出现，约 3-5s 后自然淡出；同刻多杀堆叠不横滚 ----
function drawKillfeed(ctx, time, vis) {
  if (!toggles.killfeed || !tickRateValid.value) return
  const actives = []
  for (const k of killfeedItems.value) {
    const st = Number(k.time_sec)
    if (!Number.isFinite(st)) continue
    if (time < st || time > st + KILLFEED_SEC) continue
    actives.push(k)
  }
  if (!actives.length) return
  // 稳定排序：时间升序（同刻按 seq），保证 seek 重建确定性
  actives.sort((a, b) => (Number(a.time_sec) - Number(b.time_sec)) || (Number(a.seq) - Number(b.seq)))
  // 只保留最近 MAX_KILLFEED_ROWS 条（从底部到达，向上堆叠，不横滚）
  const rows = actives.slice(-MAX_KILLFEED_ROWS)
  const pad = 14
  const rowH = 19
  const boxX = pad, boxY = pad, boxW = SIZE - pad * 2
  const boxH = rows.length * rowH + 12
  _killfeedRect = { x: boxX, y: boxY, w: boxW, h: boxH }
  ctx.save()
  ctx.fillStyle = 'rgba(11,15,20,0.62)'
  ctx.fillRect(boxX, boxY, boxW, boxH)
  rows.forEach((k, idx) => {
    const st = Number(k.time_sec)
    const frac = Math.min(1, (time - st) / KILLFEED_SEC)
    const alpha = 1 - frac * 0.72   // 自然淡出
    const ry = boxY + 8 + idx * rowH
    const seg = killfeedRow(k, ry, alpha)
    vis.killfeed = true
    void seg
  })
  ctx.restore()
}

// 单行播报：击杀者(队色) · 武器 · 受害者(队色) · [爆头/穿烟/穿墙/助攻/击杀类型]
function killfeedRow(k, ry, alpha) {
  const ctx = canvasRef.value.getContext('2d')
  const colA = teamColorOf(k.attacker_team)
  const colV = teamColorOf(k.victim_team)
  const atk = k.attacker_name || '未知'
  const vic = k.victim_name || '未知'
  const weapon = (k.weapon || '?').replace(/^weapon_/, '')
  let x = 20
  ctx.globalAlpha = alpha
  ctx.font = 'bold 12px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  // 击杀者
  ctx.fillStyle = colA; ctx.fillText(atk.slice(0, 10), x, ry); x += ctx.measureText(atk.slice(0, 10)).width + 6
  // 武器
  ctx.fillStyle = '#c9d3df'; ctx.font = '12px system-ui, sans-serif'; ctx.fillText(weapon, x, ry); x += ctx.measureText(weapon).width + 6
  // 受害者
  ctx.fillStyle = colV; ctx.font = 'bold 12px system-ui, sans-serif'; ctx.fillText(vic.slice(0, 10), x, ry); x += ctx.measureText(vic.slice(0, 10)).width + 6
  // 标记
  const marks = []
  if (k.headshot) marks.push('爆头')
  if (k.penetrated) marks.push('穿墙')
  if (k.thrusmoke) marks.push('穿烟')
  if (k.assist_name) marks.push('助攻:' + k.assist_name.slice(0, 6))
  if (k.class === 'suicide') marks.push('自杀')
  else if (k.class === 'grenade') marks.push('手雷')
  else if (k.class === 'knife') marks.push('刀杀')
  else if (k.class === 'wallbang') marks.push('穿墙')
  else if (k.class === 'thrusmoke') marks.push('穿烟')
  if (marks.length) {
    ctx.fillStyle = '#ffd24a'; ctx.font = '11px system-ui, sans-serif'; ctx.fillText(marks.join('·'), x + 14, ry)
  }
  ctx.globalAlpha = 1
}

// 玩家 id → 某时刻位置辅助
function stateById(pid, time) {
  const p = players.value.find((x) => x.player_id === pid)
  return p ? positionOf(p, time) : null
}

// ---- 真实闪光致盲：效果只来自 player_blind，与盲时长对齐 ----
function drawBlind(ctx, time, vis) {
  if (!toggles.blind || !tickRateValid.value) return
  for (const b of blindItems.value) {
    const s = Number(b.start_sec), e = Number(b.end_sec)
    if (!Number.isFinite(s) || !Number.isFinite(e)) continue
    if (time < s || time > e) continue
    const st = stateById(b.victim_player_id, time)
    if (!st) continue
    const dur = (e - s) || 1
    const frac = Math.min(1, (time - s) / dur)   // 0 → 刚致盲（最亮），1 → 结束（消失）
    const intensity = 1 - frac
    const color = BLIND_COLOR[b.flash_class] || BLIND_COLOR.self
    const cx = st.px * SIZE, cy = st.py * SIZE
    const r = 14 + intensity * 8
    ctx.save()
    // 高对比致盲环（衰减）+ 外圈辉光 + 半透明罩
    ctx.globalAlpha = 0.65 + intensity * 0.35
    ctx.strokeStyle = color; ctx.lineWidth = 3.5
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke()
    ctx.globalAlpha = intensity * 0.18; ctx.lineWidth = 7
    ctx.beginPath(); ctx.arc(cx, cy, r + 4, 0, Math.PI * 2); ctx.stroke()
    ctx.globalAlpha = intensity * 0.45; ctx.fillStyle = color
    ctx.beginPath(); ctx.arc(cx, cy, r - 2, 0, Math.PI * 2); ctx.fill()
    // 倒计时 + 自/队/敌闪标签（深色底，画面内可辨，不超出画布）
    ctx.globalAlpha = 0.95; ctx.font = 'bold 12px system-ui, sans-serif'
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    const remain = (e - time).toFixed(1)
    const label = b.flash_class ? (BLIND_LABEL[b.flash_class] || '闪') : '闪'
    const txt = `${label} ${remain}s`
    const tw = ctx.measureText(txt).width
    const ty = Math.max(14, Math.min(SIZE - 14, cy + r + 12))
    ctx.fillStyle = 'rgba(11,15,20,0.85)'
    ctx.fillRect(cx - tw / 2 - 5, ty - 9, tw + 10, 18)
    ctx.fillStyle = color
    ctx.fillText(txt, cx, ty)
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle'
    ctx.restore()
    vis.blind = true
  }
}

// ---- HE 真实受伤：瞬时爆点只是原因，高亮真实受伤玩家/伤害 ----
function drawHEDamage(ctx, time, vis) {
  if (!toggles.he || !tickRateValid.value) return
  for (const h of heDamageItems.value) {
    const st = Number(h.time_sec)
    if (!Number.isFinite(st)) continue
    if (time < st || time > st + HE_HIT_SEC) continue
    const pos = stateById(h.victim_player_id, time)
    if (!pos) continue
    const frac = Math.min(1, (time - st) / HE_HIT_SEC)
    const cx = pos.px * SIZE, cy = pos.py * SIZE
    ctx.save()
    ctx.globalAlpha = 1 - frac * 0.6
    ctx.fillStyle = '#f8b26a'; ctx.strokeStyle = '#f07828'; ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(cx, cy, 8, 0, Math.PI * 2); ctx.fill(); ctx.stroke()
    ctx.font = 'bold 12px system-ui, sans-serif'; ctx.fillStyle = '#f07828'
    ctx.fillText(`-${h.dmg_health}`, cx + 12, cy)
    ctx.restore()
    vis.hedamage = true
  }
}

function drawPropCircle(ctx, p, radius, fill, label) {
  const ax = p.rx * SIZE, ay = p.ry * SIZE
  ctx.save()
  if (label) { ctx.fillStyle = fill; ctx.globalAlpha = 1; ctx.font = 'bold 11px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.fillText(label, ax, ay + radius + 11); ctx.textAlign = 'left' }
  ctx.fillStyle = fill
  ctx.globalAlpha = 0.5
  ctx.beginPath(); ctx.arc(ax, ay, radius, 0, Math.PI * 2); ctx.fill()
  ctx.globalAlpha = 0.9
  ctx.lineWidth = 1.5; ctx.strokeStyle = fill; ctx.stroke()
  ctx.restore()
}

// 投掷者短标签（队色）+ 尾标，确保同屏多道具仍可追踪归属
function drawThrowerLabel(ctx, rx, ry, name, team, color, dy = 0) {
  if (!name) return
  const x = rx * SIZE, y = ry * SIZE
  ctx.save()
  ctx.font = 'bold 11px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  const w = ctx.measureText(name.slice(0, 8)).width
  ctx.fillStyle = 'rgba(11,15,20,0.82)'
  ctx.fillRect(x - w / 2 - 4, y + dy - 8, w + 8, 16)
  ctx.fillStyle = color
  ctx.textAlign = 'center'
  ctx.fillText(name.slice(0, 8), x, y + dy)
  ctx.textAlign = 'left'
  ctx.restore()
}

// 绘制道具生命周期；vis 用于记录当前是否有烟/火激活（写 data-smoke/data-fire 钩子）
function drawProps(ctx, currentTime, vis) {
  for (const pr of propItems.value) {
    if (pr.rx == null || pr.ry == null) continue
    const throwerColor = teamColorOf(pr.thrower_team)
    // 投掷轨迹（可选，默认关）
    if (toggles.trajectory && Array.isArray(pr.trajectory) && pr.trajectory.length > 1) {
      drawPath(ctx, pr.trajectory, PROP_COLOR.trajectory, 1.5, 0.5, [4, 4])
    }
    if (pr.type === 'smoke' || pr.type === 'fire') {
      if (!(pr.type === 'smoke' ? toggles.smoke : toggles.fire)) continue
      const s = pr.active_start_sec, e = pr.active_end_sec
      const sn = s == null || s === '' ? NaN : Number(s)
      const en = e == null || e === '' ? NaN : Number(e)
      if (!Number.isFinite(sn) || !Number.isFinite(en)) continue // 缺失 → 永不出现（fail closed）
      if (currentTime < sn || currentTime > en) continue          // 只在有效区间
      if (pr.type === 'smoke') { drawPropCircle(ctx, pr, 40, PROP_COLOR.smoke, '烟'); vis.smoke = true }
      else { drawPropCircle(ctx, pr, 30, PROP_COLOR.fire, '火'); vis.fire = true }
      // 投掷者归属：队色 + 名称短标签，落在烟/火中心下方
      drawThrowerLabel(ctx, pr.rx, pr.ry, pr.thrower_name, pr.thrower_team, throwerColor,
                       pr.type === 'smoke' ? 30 : 44)
      continue
    }
    if (pr.type === 'he' || pr.type === 'flash') {
      if (!(pr.type === 'he' ? toggles.he : toggles.flash)) continue
      const s = pr.active_start_sec
      const sn = s == null || s === '' ? NaN : Number(s)
      if (!Number.isFinite(sn)) continue // 缺失 → 永不出现
      const dt = currentTime - sn
      if (dt < 0 || dt > FLASH_SEC) continue // 瞬时反馈窗口
      const frac = dt / FLASH_SEC
      if (pr.type === 'he') {
        const r = 6 + frac * 26
        ctx.save(); ctx.globalAlpha = 1 - frac * 0.6
        ctx.strokeStyle = PROP_COLOR.he; ctx.lineWidth = 3
        ctx.beginPath(); ctx.arc(pr.rx * SIZE, pr.ry * SIZE, r, 0, Math.PI * 2); ctx.stroke(); ctx.restore()
        drawThrowerLabel(ctx, pr.rx, pr.ry, pr.thrower_name, pr.thrower_team, throwerColor, 14)
      } else {
        const r = 8 + frac * 24
        ctx.save(); ctx.globalAlpha = 1 - frac * 0.5
        const g = ctx.createRadialGradient(pr.rx * SIZE, pr.ry * SIZE, 0, pr.rx * SIZE, pr.ry * SIZE, r)
        g.addColorStop(0, PROP_COLOR.flash); g.addColorStop(1, 'rgba(255,255,255,0)')
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(pr.rx * SIZE, pr.ry * SIZE, r, 0, Math.PI * 2); ctx.fill(); ctx.restore()
        drawThrowerLabel(ctx, pr.rx, pr.ry, pr.thrower_name, pr.thrower_team, throwerColor, 14)
      }
    }
  }
}

function drawPlayer(ctx, player, teamColor, alpha, isFocus, staticMode, vis) {
  const pts = player.points
  if (!pts.length) return
  const cTick = freezeTick.value + currentTime.value * tickRate.value
  const state = playerState(player, currentTime.value)

  if (staticMode) {
    // 时长不可核算：完整路线 + 所属玩家终态（不伪造时间/当前球/枪线）
    if (toggles.route) drawPath(ctx, pts, teamColor, 2.2, alpha)
    return
  }

  // 路线（已探索到当前时刻）：用二分定位「已探索」末下标，避免每帧从头线性扫描全部点。
  // 绘制用预计算简化折线（误差上限 ROUTE_SIM_EPS_PX<=2px）；若该玩家无简化（点数少/无）则用原始 points，保证精度不因简化丢失。
  if (toggles.route) {
    ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = teamColor; ctx.lineWidth = 2.2
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.beginPath()
    const rpts = player.__sim || pts
    const end = exploredEndIndex(rpts, cTick)
    let started = false
    for (let i = 0; i <= end; i++) {
      const p = rpts[i]
      const x = p.rx * SIZE, y = p.ry * SIZE
      if (!started) { ctx.moveTo(x, y); started = true } else ctx.lineTo(x, y)
    }
    if (started && state.px != null) ctx.lineTo(state.px * SIZE, state.py * SIZE)
    ctx.stroke(); ctx.restore()
  }

  // 存活终局方块（仅存活玩家、且已到回合末）
  if (player.outcome === 'alive' && round.value.end_tick <= cTick && pts.length) {
    const last = pts[pts.length - 1]; ctx.fillStyle = MARK.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
  }

  const live = isLiveAt(player, cTick)
  if (state.px != null && live) {
    // 枪线（在球/标签之下）
    if (toggles.gun && state.yaw != null) { drawGunFan(ctx, state.px, state.py, state.yaw, teamColor); vis.gun = true }
    // 当前球 + 焦点白环 + 短名/编号 + 本回合真实阵营
    drawDot(ctx, state.px, state.py, 6, MARK.ball, teamColor)
    if (isFocus) { ctx.lineWidth = 2.5; ctx.strokeStyle = MARK.focusRing; ctx.beginPath(); ctx.arc(state.px * SIZE, state.py * SIZE, 9, 0, Math.PI * 2); ctx.stroke() }
    if (toggles.playerName) collectLabel({
      id: 'p_' + player.player_id,
      ax: state.px * SIZE, ay: state.py * SIZE,
      text: `${(player.name || 'P' + player.player_id).slice(0, 3)}·${player.side || '—'}`,
      color: teamColor,
      priority: player.player_id === focusPlayerId.value ? 100 : 70,
    })
  }

  // 阵亡 X（仅阵亡玩家，在其 alive 翻转/末点处；death tick 已预计算，避免每帧线性扫描）
  if (player.outcome === 'dead') {
    const dt = deathTickOf(player)
    if (dt != null && dt <= cTick) {
      let dp = pts[pts.length - 1]
      for (const p of pts) if (p.alive === false) { dp = p; break }
      ctx.save(); ctx.globalAlpha = alpha; drawX(ctx, dp.rx, dp.ry, 7, MARK.death); ctx.restore()
    }
  }
}

function drawLegend(ctx) {
  const items = [
    { c: TEAM_COLOR.A, shape: 'dot', label: '队伍 A' },
    { c: TEAM_COLOR.B, shape: 'dot', label: '队伍 B' },
    { c: MARK.death, shape: 'x', label: '阵亡' },
    { c: MARK.ball, shape: 'ball', label: '当前' },
    { c: MARK.focusRing, shape: 'ring', label: '焦点（我）' },
  ]
  if (toggles.area) {
    items.push({ c: AREA_STROKE.occupiedA, shape: 'area', label: '占据（未证实控制）' })
    items.push({ c: AREA_STROKE.controlledA, shape: 'area', label: '控制（有正向信号）' })
    items.push({ c: AREA_STROKE.contested, shape: 'area', label: '争夺（敌我同区）' })
    items.push({ c: AREA_STROKE.influenceA, shape: 'area', label: '区域影响（试验）' })
  }
  if (toggles.gun) { items.push({ c: '#e8a13c', shape: 'gun', label: '视角朝向' }); items.push({ c: '#ffd24a', shape: 'shoot', label: '射击线（已验证）' }) }
  if (toggles.smoke) items.push({ c: PROP_COLOR.smoke, shape: 'smoke', label: '烟' })
  if (toggles.fire) items.push({ c: PROP_COLOR.fire, shape: 'fire', label: '火' })
  if (toggles.flash) items.push({ c: PROP_COLOR.flash, shape: 'flash', label: '闪' })
  if (toggles.he) items.push({ c: PROP_COLOR.he, shape: 'he', label: '手雷' })
  if (toggles.blind) items.push({ c: BLIND_COLOR.enemy, shape: 'blind', label: '致盲（自/队/敌）' })
  if (toggles.he) items.push({ c: '#f07828', shape: 'hedamage', label: 'HE 伤害' })
  if (toggles.killfeed) items.push({ c: '#ffd24a', shape: 'killfeed', label: '击杀播报' })
  const pad = 14; const boxW = 184; const boxH = items.length * 17 + 16
  const bx = pad; const by = SIZE - boxH - pad
  _legendRect = { x: bx, y: by, w: boxW, h: boxH }
  ctx.fillStyle = 'rgba(11,15,20,0.72)'; ctx.fillRect(bx, by, boxW, boxH)
  ctx.font = '11px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  items.forEach((it, i) => {
    const ly = by + 14 + i * 17, lx = bx + 14
    if (it.shape === 'dot') drawDot(ctx, lx / SIZE, ly / SIZE, 3.5, it.c)
    else if (it.shape === 'x') drawX(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'ball') drawDot(ctx, lx / SIZE, ly / SIZE, 5, it.c, TEAM_COLOR.A)
    else if (it.shape === 'ring') { ctx.lineWidth = 2; ctx.strokeStyle = it.c; ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI * 2); ctx.stroke() }
    else if (it.shape === 'gun') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(lx - 5, ly + 4); ctx.lineTo(lx + 5, ly - 4); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'shoot') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(lx - 6, ly + 5); ctx.lineTo(lx + 6, ly - 5); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'smoke' || it.shape === 'fire') { ctx.save(); ctx.globalAlpha = 0.5; ctx.fillStyle = it.c; ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI * 2); ctx.fill(); ctx.restore() }
    else if (it.shape === 'flash') { ctx.save(); ctx.globalAlpha = 0.7; ctx.fillStyle = it.c; ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.fill(); ctx.restore() }
    else if (it.shape === 'he') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'blind') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI * 2); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'hedamage') { ctx.save(); ctx.fillStyle = it.c; ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.fill(); ctx.restore() }
    else if (it.shape === 'killfeed') { ctx.save(); ctx.fillStyle = it.c; ctx.fillRect(lx - 5, ly - 3, 10, 6); ctx.restore() }
    else if (it.shape === 'area') { ctx.save(); ctx.globalAlpha = 0.35; ctx.fillStyle = it.c; ctx.fillRect(lx - 6, ly - 5, 12, 10); ctx.globalAlpha = 0.95; ctx.strokeStyle = it.c; ctx.lineWidth = 1.5; ctx.strokeRect(lx - 6, ly - 5, 12, 10); ctx.restore() }
    ctx.fillStyle = '#e8edf4'; ctx.fillText(it.label, lx + 12, ly)
  })
}

// 区域控制图层：叠加真实 last_place_name 多边形，按当前时刻状态着色（CT/T + 争夺第三态）。
// 只在当前 team.area_state_machine 提供该区域/该队的实时状态段时着色；无状态（unknown）不绘制轮廓。
function drawAreas(ctx, t) {
  const geomByKey = _geomByKeyCache
  // 当前时刻该区域应展示的状态 / 归属队伍 / 持续秒（由 area_state_machine 的 segments 提供）
  // area_state_machine: [{round, team, place_key, place_cn, segments:[{start_sec,end_sec,state,...}]}]
  const byKey = {}
  for (const sm of areaStateMachine.value) {
    if (sm?.round !== selectedRound.value) continue
    let best = null
    for (const seg of sm.segments || []) {
      const s = Number(seg.start_sec), e = Number(seg.end_sec)
      if (Number.isFinite(s) && t >= s && t <= e) { if (!best || (seg.state !== 'unknown')) best = seg }
    }
    if (best && best.state && best.state !== 'unknown') {
      const k = `${sm.place_key}|${sm.team}`
      byKey[k] = { ...best, team: sm.team, place_key: sm.place_key, place_cn: sm.place_cn }
    }
  }
  for (const k in byKey) {
    const it = byKey[k]
    const geom = geomByKey[it.place_key]
    if (!geom) continue
    let fillKey
    if (it.state === 'contested') fillKey = 'contested'
    else if (it.state === 'controlled') fillKey = `controlled${it.team}`
    else if (it.state === 'occupied') fillKey = `occupied${it.team}`
    else if (it.state === 'influence') fillKey = `influence${it.team}`
    else continue
    // 用凸包多边形填充 + 轮廓 + 中文叫位 + 状态/持续秒
    const poly = geom.polygon
    ctx.save()
    ctx.globalAlpha = 0.9
    ctx.fillStyle = AREA_STATE_COLOR[fillKey] || 'transparent'
    ctx.strokeStyle = AREA_STROKE[fillKey] || '#888'
    ctx.lineWidth = it.state === 'controlled' ? 3 : 1.6
    ctx.beginPath()
    ctx.moveTo(poly[0].rx * SIZE, poly[0].ry * SIZE)
    for (let i = 1; i < poly.length; i++) ctx.lineTo(poly[i].rx * SIZE, poly[i].ry * SIZE)
    ctx.closePath()
    ctx.fill()
    ctx.stroke()
    // 中文叫位 + 状态/持续
    const c = geom.centroid || {}
    if (c.rx != null && c.ry != null) {
      const label = `${it.place_cn || it.place_key} · ${AREA_STATE_CN[it.state] || it.state}${it.dur ? ` ${Math.round(it.dur)}s` : ''}`
      ctx.globalAlpha = 1
      collectLabel({
        id: 'ra_' + it.place_key + '_' + it.team,
        ax: c.rx * SIZE, ay: c.ry * SIZE,
        text: label, color: '#edf1f6',
        priority: it.state === 'contested' ? 60 : it.state === 'controlled' ? 52 : 44,
      })
    }
    ctx.restore()
  }
}

// ---- 6R18 动态区域控制：连续控制场 + 前线 ---------------------------------------
// 诚实原则：只用 6R16 真实 unknown/occupied/contested/influence/controlled 状态机 + 版本化区域几何。
// 连续场 = 真实凸包区域并集成可通行掩膜网格，逐格按当前时刻控制状态着色；上采样做平滑 → 随 t 扩张/收缩。
// 前线 = 控制阵营在网格邻接处翻转的单元（亮线 + 呼吸），全部由真实状态机驱动（不伪造 LoS/拓扑/因果）。
// 低 coverage/confidence / 非几何区域 → 降级为离散多边形/中心虚线（drawAreas），绝不凭空填充。
const AREA_GRID_N = 96
const FIELD_CT = [88, 166, 255]
const FIELD_T = [240, 160, 32]
const FIELD_CONTESTED = [226, 165, 74]
const STATE_STRENGTH = { controlled: 1.0, occupied: 0.72, influence: 0.4, contested: 0.8, unknown: 0 }
let areaField = null        // { N, cells, geomByIndex }
let areaFieldSig = ''
let fieldCanvas = null

function _ramp(x) { return x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x) }
function _pointInPoly(x, y, poly) {
  if (poly.length < 3) return false
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1]
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
function buildFieldGrid(geo) {
  const geometric = (geo?.regions || []).filter((r) => r.geometric && (r.polygon || []).length >= 3)
  if (!geometric.length) return null
  const polys = geometric.map((r) => r.polygon.map((p) => [p.rx, p.ry]))
  const N = AREA_GRID_N
  const cells = new Int16Array(N * N).fill(-1)
  for (let gy = 0; gy < N; gy++) {
    const ry = (gy + 0.5) / N
    for (let gx = 0; gx < N; gx++) {
      const rx = (gx + 0.5) / N
      for (let i = 0; i < polys.length; i++) {
        if (_pointInPoly(rx, ry, polys[i])) { cells[gy * N + gx] = i; break }
      }
    }
  }
  return { N, cells, geomByIndex: geometric, regionCount: polys.length }
}
// 某队状态机在 t 时刻的连续"控制强度"（0~1，分段间用 smoothstep 交叉淡入淡出，确定性）。
function teamStrength(segments, t, blend = 0.6) {
  if (!segments || !segments.length) return 0
  const str = (i) => (i >= 0 && i < segments.length) ? (STATE_STRENGTH[segments[i].state] || 0) : 0
  let idx = -1
  for (let i = 0; i < segments.length; i++) if (t >= segments[i].start_sec && t <= segments[i].end_sec) { idx = i; break }
  if (idx < 0) {
    let best = -1, bd = Infinity
    for (let i = 0; i < segments.length; i++) {
      const s = segments[i], d = t < s.start_sec ? s.start_sec - t : t - s.end_sec
      if (d < bd) { bd = d; best = i }
    }
    if (best < 0) return 0
    return str(best) * _ramp(1 - bd / blend)
  }
  let v = str(idx)
  const s = segments[idx]
  const dStart = t - s.start_sec, dEnd = s.end_sec - t
  if (idx > 0 && dStart < blend) v = str(idx - 1) * (1 - _ramp(dStart / blend)) + v * _ramp(dStart / blend)
  if (idx < segments.length - 1 && dEnd < blend) v = v * _ramp(dEnd / blend) + str(idx + 1) * (1 - _ramp(dEnd / blend))
  return v
}
function stateAt(segments, t) {
  if (!segments) return null
  for (const s of segments) if (t >= s.start_sec && t <= s.end_sec) return s.state
  return null
}
function regionFieldColor(region, t) {
  const pk = region.place_key
  let sA, sB
  for (const sm of areaStateMachine.value) {
    if (sm?.round !== selectedRound.value || sm.place_key !== pk) continue
    if (sm.team === 'A') sA = sm.segments
    else if (sm.team === 'B') sB = sm.segments
  }
  const stA = stateAt(sA, t), stB = stateAt(sB, t)
  if (stA === 'contested' || stB === 'contested') return { camp: 'contested', state: 'contested', strength: 0.75 }
  const a = teamStrength(sA, t), b = teamStrength(sB, t)
  if (a <= 0.02 && b <= 0.02) return null
  if (a >= b) return { camp: 'A', state: stA || 'occupied', strength: a }
  return { camp: 'B', state: stB || 'occupied', strength: b }
}
function regionControlDur(pk, t) {
  let best = null
  for (const sm of areaStateMachine.value) {
    if (sm?.round !== selectedRound.value || sm.place_key !== pk) continue
    for (const s of sm.segments || []) if (t >= s.start_sec && t <= s.end_sec && s.state !== 'unknown' && (!best || s.dur > best.dur)) best = s
  }
  return best ? best.dur : null
}
function confidenceCN(r) {
  if (r && r.geometric === false) return '样本不足'
  const c = r && r.confidence
  return c === 'high' ? '高置信' : c === 'medium' ? '中置信' : c === 'low' ? '低置信' : '—'
}
function drawControlField(ctx, t) {
  const geo = regionGeometry.value
  if (!areaField || areaFieldSig !== (geo?.content_hash || '')) {
    areaFieldSig = geo?.content_hash || ''
    areaField = buildFieldGrid(geo)
    fieldCanvas = null
  }
  if (!areaField) return false
  const { N, cells, geomByIndex } = areaField
  const colors = new Array(areaField.regionCount)
  const regionCamps = new Uint8Array(areaField.regionCount)   // 0 none, 1 CT(A), 2 T(B), 3 contested
  for (let i = 0; i < areaField.regionCount; i++) {
    const rc = regionFieldColor(geomByIndex[i], t)
    if (!rc) { colors[i] = null; regionCamps[i] = 0; continue }
    if (rc.camp === 'contested') {
      colors[i] = [FIELD_CONTESTED[0], FIELD_CONTESTED[1], FIELD_CONTESTED[2], Math.round(70 + rc.strength * 70)]
      regionCamps[i] = 3
    } else if (rc.camp === 'A') {
      colors[i] = [FIELD_CT[0], FIELD_CT[1], FIELD_CT[2], Math.round(40 + rc.strength * 95)]
      regionCamps[i] = 1
    } else {
      colors[i] = [FIELD_T[0], FIELD_T[1], FIELD_T[2], Math.round(40 + rc.strength * 95)]
      regionCamps[i] = 2
    }
  }
  if (!fieldCanvas) { fieldCanvas = document.createElement('canvas'); fieldCanvas.width = N; fieldCanvas.height = N }
  const fctx = fieldCanvas.getContext('2d')
  const img = fctx.createImageData(N, N)
  const d = img.data
  const camps = new Uint8Array(N * N)
  for (let k = 0; k < cells.length; k++) {
    const idx = cells[k], col = idx >= 0 ? colors[idx] : null, o = k * 4
    if (col) {
      d[o] = col[0]; d[o + 1] = col[1]; d[o + 2] = col[2]; d[o + 3] = col[3]
      camps[k] = idx >= 0 ? regionCamps[idx] : 0
    } else { d[o] = d[o + 1] = d[o + 2] = d[o + 3] = 0; camps[k] = 0 }
  }
  // 前线：控制阵营在网格邻接处翻转的单元（真实状态机驱动），呼吸柔化
  const breathe = reducedMotion.value ? 0.5 : 0.5 + 0.5 * Math.sin(t * 2.2)
  let frontierCount = 0
  for (let gy = 0; gy < N; gy++) {
    for (let gx = 0; gx < N; gx++) {
      const k = gy * N + gx
      if (!camps[k]) continue
      const rk = gy * N + (gx + 1), bk = (gy + 1) * N + gx
      let isLine = false
      if (gx < N - 1 && camps[rk] && camps[rk] !== camps[k]) isLine = true
      else if (gy < N - 1 && camps[bk] && camps[bk] !== camps[k]) isLine = true
      if (isLine) frontierCount++
    }
  }
  fctx.putImageData(img, 0, 0)
  ctx.save()
  ctx.imageSmoothingEnabled = true
  ctx.drawImage(fieldCanvas, 0, 0, N, N, 0, 0, SIZE, SIZE)
  ctx.restore()
  // 前线（清晰版）：在 720 画布上按阵营邻接翻转画高亮短段，随呼吸柔化；只画真实 CT/T 或争夺边界，不凭空画。
  ctx.save()
  ctx.lineWidth = 3.2
  ctx.lineCap = 'round'
  ctx.strokeStyle = `rgba(255,209,120,${(0.62 + 0.33 * breathe).toFixed(3)})`
  const cell = SIZE / N
  for (let gy = 0; gy < N; gy++) {
    for (let gx = 0; gx < N; gx++) {
      const k = gy * N + gx
      if (!camps[k]) continue
      const cx = (gx + 0.5) * cell, cy = (gy + 0.5) * cell
      const rk = gy * N + (gx + 1), bk = (gy + 1) * N + gx
      if (gx < N - 1 && camps[rk] && camps[rk] !== camps[k]) { ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + cell, cy); ctx.stroke() }
      if (gy < N - 1 && camps[bk] && camps[bk] !== camps[k]) { ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy + cell); ctx.stroke() }
    }
  }
  ctx.restore()
  // 画布可读信息：中文区域 + 控制状态 + 持续秒 + 置信
  for (let i = 0; i < areaField.regionCount; i++) {
    const r = geomByIndex[i]
    const rc = regionFieldColor(r, t)
    if (!rc) continue
    const dur = regionControlDur(r.place_key, t)
    const label = `${r.place_cn || r.place_key} · ${AREA_STATE_CN[rc.state] || rc.state}${dur ? ` ${Math.round(dur)}s` : ''} · ${confidenceCN(r)}`
    if (r.centroid && r.centroid.rx != null && r.centroid.ry != null) collectLabel({
      id: 'rc_' + r.place_key,
      ax: r.centroid.rx * SIZE, ay: r.centroid.ry * SIZE,
      text: label, color: '#edf1f6',
      priority: rc.state === 'contested' ? 60 : rc.state === 'controlled' ? 52 : rc.state === 'occupied' ? 46 : 38,
    })
  }
  // 确定性测试钩子：阵营网格计数[CT,T,contested] + 前线格子数 + 阵营网格哈希（供 E2E 严格校验真实 CT/T 前线，杜绝造假）
  if (canvasRef.value) {
    let c1 = 0, c2 = 0, c3 = 0, h = 0
    for (let k = 0; k < camps.length; k++) {
      const c = camps[k]
      if (c === 1) c1++
      else if (c === 2) c2++
      else if (c === 3) c3++
      h = (h * 31 + c) % 1000000007
    }
    canvasRef.value.setAttribute('data-area-camps', `${c1},${c2},${c3}`)
    canvasRef.value.setAttribute('data-area-frontier', String(frontierCount))
    canvasRef.value.setAttribute('data-area-hash', String(h))
  }
  return true
}
// 区域控制图层分发：几何信任 validated（高 coverage）→ 连续控制场；experimental/低 confidence → 降级为离散多边形/中心虚线。
function drawControlLayer(ctx, t) {
  const geo = regionGeometry.value
  const geometric = (geo?.regions || []).filter((r) => r.geometric && (r.polygon || []).length >= 3)
  const trusted = geo?.geometry_trust?.trust === 'validated'
  if (geometric.length >= 4 && trusted && drawControlField(ctx, t)) return 'field'
  if (geometric.length) drawAreas(ctx, t)
  return 'poly'
}

function render() {
  const canvas = canvasRef.value; if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, SIZE, SIZE)
  if (radarImg.value) ctx.drawImage(radarImg.value, 0, 0, SIZE, SIZE)
  const t = currentTime.value
  const vis = { gun: false, smoke: false, fire: false, shoot: false, blind: false, killfeed: false, hedamage: false }
  _labelAnchors = []
  _labelBlocked = []
  _killfeedRect = null
  _legendRect = null

  // 区域控制图层（地图之上、玩家之下；与时间轴联动，seek 确定性）
  // 6R18：几何可信 → 连续控制场+前线；coverage/confidence 不足 → 降级为离散多边形/中心虚线
  let areaMode = ''
  if (toggles.area && tickRateValid.value) areaMode = drawControlLayer(ctx, t)
  if (areaMode !== 'field') {
    // 非连续场（离散降级/关闭）不产生 camp 网格 → 清空测试钩子，避免陈旧值
    canvas.setAttribute('data-area-camps', '')
    canvas.setAttribute('data-area-frontier', '')
    canvas.setAttribute('data-area-hash', '')
  }

  // 道具（玩家之下）
  if (tickRateValid.value) drawProps(ctx, t, vis)

  // 瞬时"射击线"（仅在 validated + 时间窗内；direction_available=false → 永不渲染，诚实）
  if (tickRateValid.value && toggles.gun) {
    for (const s of shootItems.value) {
      if (!s || !s.validated || !(s.rx != null && s.ry != null && s.dir_x != null && s.dir_y != null)) continue
      const st = Number(s.time)
      if (!Number.isFinite(st)) continue
      if (Math.abs(t - st) <= SHOOT_WINDOW) { drawShootLine(ctx, s, '#ffd24a'); vis.shoot = true }
    }
  }

  const shown = shownPlayers()
  for (const player of shown) {
    const teamColor = TEAM_COLOR[player.team_identity] || '#9aa7b8'
    const isHi = highlight.value == null || highlight.value === player.player_id
    const isFocus = player.player_id === focusPlayerId.value
    drawPlayer(ctx, player, teamColor, isHi ? ROUTE_ALPHA : FADED_ALPHA, isFocus, !tickRateValid.value, vis)
  }

  // 6R13：真实致盲（绑定受害者当前球）、HE 受伤、击杀播报（在有玩家之上、图例之下）
  if (tickRateValid.value) {
    drawBlind(ctx, t, vis)
    drawHEDamage(ctx, t, vis)
    drawKillfeed(ctx, t, vis)
  }
  drawLegend(ctx)

  // 6R21C：收集到全部标签后统一做确定性二维布局/避让（面板矩形作为 blocked 避开击杀播报/图例）
  _labelBlocked = [_killfeedRect, _legendRect].filter(Boolean)
  drawLabels(ctx)

  // 测试钩子
  canvas.setAttribute('data-ready', 'ready')
  canvas.setAttribute('data-gun', vis.gun ? '1' : '')
  canvas.setAttribute('data-shoot', vis.shoot ? '1' : '')
  canvas.setAttribute('data-smoke', vis.smoke ? '1' : '')
  canvas.setAttribute('data-fire', vis.fire ? '1' : '')
  canvas.setAttribute('data-blind', vis.blind ? '1' : '')
  canvas.setAttribute('data-killfeed', vis.killfeed ? '1' : '')
  canvas.setAttribute('data-hedamage', vis.hedamage ? '1' : '')
  canvas.setAttribute('data-time', String(Math.round(t * 100) / 100))
  canvas.setAttribute('data-area', areaMode)
}

let renderPending = false
function scheduleRender(retries = 0) {
  if (renderPending) return; renderPending = true
  nextTick(() => {
    renderPending = false
    if (canvasRef.value) { render(); return }
    if (retries < 40) scheduleRender(retries + 1)
  })
}

function tick(ts) {
  if (!playing.value) return
  if (lastTs) {
    const dt = (ts - lastTs) / 1000
    currentTime.value = Math.min(currentTime.value + dt * speed.value, maxDuration.value)
    if (currentTime.value >= maxDuration.value) { currentTime.value = maxDuration.value; playing.value = false }
    render()
  }
  lastTs = ts
  if (playing.value) rafId = requestAnimationFrame(tick)
}
function play() { if (reducedMotion.value || !maxDuration.value) return; if (currentTime.value >= maxDuration.value) currentTime.value = 0; playing.value = true; lastTs = 0; rafId = requestAnimationFrame(tick) }
function pause() { playing.value = false; if (rafId) cancelAnimationFrame(rafId) }
function togglePlay() { playing.value ? pause() : play() }
function setSpeed(v) { speed.value = v }
function onScrub(e) { pause(); currentTime.value = Math.min(Number(e.target.value), maxDuration.value); render() }
function setHighlight(pid) { highlight.value = highlight.value === pid ? null : pid }
function resetPlayback() { pause(); currentTime.value = 0; render() }

async function loadRadar() {
  const img = new Image(); img.crossOrigin = 'anonymous'; img.src = `/api/maps/${props.mapName}/radar`
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej })
  radarImg.value = img; scheduleRender()
}
async function loadRound() {
  const gen = ++loadGeneration
  loading.value = true; error.value = ''
  try {
    const data = await api.get(`/api/demos/${props.demoId}/team/replay?round_no=${selectedRound.value}`)
    if (gen !== loadGeneration) return
    team.value = data
    // 6R21F-F3P：每回合一次性预计算派生索引（死亡 tick、简化路线、区域几何），消除每帧线性扫描/重建。
    buildRoundIndexes()
    _geomByKeyCache = {}
    for (const r of (regionGeometry.value?.regions || [])) {
      if (r?.place_key && r?.polygon?.length >= 3) _geomByKeyCache[r.place_key] = r
    }
    _shownCache = null; _shownCacheKey = ''
    highlight.value = null
    pause(); currentTime.value = 0
    // 若由深度配合定位而来，应用目标时刻 + 图层 + 高亮
    if (pendingLocate && pendingLocate.round === selectedRound.value) {
      const pl = pendingLocate; pendingLocate = null
      applyLocateLayers(pl)
      if (tickRateValid.value && maxDuration.value > 0) {
        currentTime.value = Math.min(Math.max(0, Number(pl.time) || 0), maxDuration.value)
      }
    }
    scheduleRender()
  } catch (e) {
    if (gen !== loadGeneration) return
    error.value = e instanceof ApiError ? e.message : '团队同步回放加载失败'
    team.value = null
  } finally {
    if (gen === loadGeneration) { loading.value = false; scheduleRender() }
  }
}
function selectRound(v) { selectedRound.value = Number(v); loadRound() }
function setFilter(f) { filter.value = f }

// 对外暴露：由深度配合分析 / 6R15 长期教练证据跳转驱动，切换到指定回合并 scrub 到时刻，且自动开启所需图层、高亮相关玩家。
function locate(round, time, opts = {}) {
  pendingLocate = {
    round: Number(round), time: Number(time),
    layers: Array.isArray(opts?.layers) ? opts.layers : [],
    highlight: Array.isArray(opts?.highlight) ? opts.highlight : [],
  }
  // 目标回合与当前不同、或首回合尚未加载 → 走重新加载路径（pendingLocate 在 loadRound 内应用）。
  if (Number(round) !== selectedRound.value || !team.value) {
    selectedRound.value = Number(round); loadRound()
  }
  else {
    // 已在该回合：直接 scrub + 应用图层/高亮
    applyLocateLayers(pendingLocate)
    pause()
    currentTime.value = Math.min(Math.max(0, Number(time) || 0), maxDuration.value)
    pendingLocate = null
    render()
  }
}
function applyLocateLayers(pl) {
  for (const l of pl.layers) if (l in toggles) toggles[l] = true
  const hids = pl.highlight || []
  highlight.value = hids.length ? hids[0] : null
}
defineExpose({ locate })

watch([radarImg, players, team, highlight, filter, toggles], () => scheduleRender())
watch(() => props.maxRound, () => { if (props.maxRound > 0) selectedRound.value = 1 })

onMounted(async () => { if (props.maxRound > 0) { try { await loadRadar() } catch { /* 忽略 */ } await loadRound() } detectReducedMotion() })
onBeforeUnmount(() => { pause() })
</script>

<template>
  <div class="tm" :data-side="round.side" :data-reduced-motion="String(reducedMotion)">
    <div v-if="loading" class="tm-state">加载团队同步回放…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else-if="team">
      <!-- 6R17.2：紧凑工具栏（回合/队伍/本回合/倍速 单行），地图成为视觉主角 -->
      <div class="tm-toolbar">
        <label class="tm-field"><span class="tm-lbl">回合</span>
          <select class="input tm-sel" :value="selectedRound" @change="selectRound($event.target.value)">
            <option v-for="n in Math.max(1, props.maxRound)" :key="n" :value="n">R{{ n }}</option>
          </select>
        </label>
        <div class="tm-seg" role="group" aria-label="队伍筛选">
          <button v-for="f in [['all', '全部'], ['A', '队伍A'], ['B', '队伍B']]" :key="f[0]" class="tm-seg-btn"
            :class="{ on: filter === f[0] }" @click="setFilter(f[0])">{{ f[1] }}</button>
        </div>
        <span class="tm-summary tm-sum-inline num">本回合 <b>{{ summary.a }}</b> 队A : <b>{{ summary.b }}</b> 队B · 存活 <b>{{ summary.alive }}</b> · 胜方 {{ winner === 'A' ? '队伍A' : winner === 'B' ? '队伍B' : '?' }}</span>
        <div class="tm-spacer"></div>
        <div class="tm-seg" role="group" aria-label="倍速">
          <button v-for="v in [0.5, 1, 2]" :key="v" class="tm-seg-btn sm" :class="{ on: speed === v }" @click="setSpeed(v)">{{ v }}x</button>
        </div>
      </div>

      <!-- 主体：主地图（主角）+ 球员侧栏 -->
      <div class="tm-body">
        <div class="tm-main">
          <!-- 播放/seek 是回放首要操作：DOM 与视觉顺序都先于可选图层，保证移动端首屏可达。 -->
          <div class="tm-timeline">
            <button class="tm-play" :disabled="reducedMotion || !tickRateValid" @click="togglePlay" :aria-label="playing ? '暂停' : '播放'">{{ reducedMotion ? '⏵' : (playing ? '⏸' : '⏵') }}</button>
            <input class="tm-scrub" type="range" min="0" :max="tickRateValid ? maxDuration : 0" step="0.01" :value="currentTime" @input="onScrub" :disabled="!tickRateValid" aria-label="时间轴" />
            <span class="tm-time num" v-if="tickRateValid">当前 {{ fmtTime(currentTime) }} / {{ fmtTime(maxDuration) }}</span>
            <span class="tm-time num" v-else>时长不可核算</span>
          </div>

          <!-- 图层开关：桌面地图上方紧凑换行；移动端单行触控横滑（隐藏滚动条，避免粗重横滚挤压地图/播放首屏） -->
          <div class="tm-toggles" role="group" aria-label="图层开关">
            <button v-for="t in [
              { key: 'playerName', label: '玩家名', color: '#e7ecf2' },
              { key: 'route', label: '路线', color: '#58a6ff' },
              { key: 'gun', label: '视角朝向', color: '#e8a13c' },
              { key: 'smoke', label: '烟', color: '#96aabe' },
              { key: 'fire', label: '火', color: '#f07828' },
              { key: 'flash', label: '闪', color: '#ffffff' },
              { key: 'he', label: '手雷', color: '#f0c83c' },
              { key: 'trajectory', label: '投掷轨迹', color: '#7d8c9e' },
              { key: 'killfeed', label: '击杀播报', color: '#ffd24a' },
              { key: 'blind', label: '真实致盲', color: '#f85149' },
              { key: 'area', label: '区域控制', color: '#e6a54a' },
            ]" :key="t.key" class="tm-toggle" :class="{ on: toggles[t.key] }"
              :aria-pressed="toggles[t.key]" @click="toggles[t.key] = !toggles[t.key]">
              <span class="tm-toggle-dot" :style="{ background: t.color }"></span>{{ t.label }}
            </button>
          </div>

          <div class="tm-stage">
            <canvas ref="canvasRef" :width="SIZE" :height="SIZE" class="tm-canvas" data-ready="ready"></canvas>
            <div v-if="currentPlace" class="tm-region">当前区域：{{ currentPlace }}</div>
            <p v-if="reducedMotion" class="tm-hint">已开启减弱动效：拖动时间轴查看。</p>
            <p v-if="!tickRateValid" class="tm-hint">后端未提供可证明的 tick_rate，本回合时长无法核算。</p>
          </div>

          <!-- 口径说明（收进折叠，不占首屏/不遮图层开关） -->
          <details class="tm-legend fold">
            <summary>口径说明</summary>
            <p class="tm-hint">视角朝向为连续插值眼朝向（非移动方向）；瞬时「射击线」仅在直接可见击杀且通过真值门禁时显示；击杀播报来自真实 player_death；致盲来自 player_blind（盲时长真实）；HE 受伤为 thrower+weapon+time-window 关联（derived）；区域控制为可信状态机：占据≠控制（控制需正向信号）、争夺=敌我同区、区域影响=实验性；坐标/tick/角度见下方折叠诊断。</p>
          </details>

          <!-- 诊断信息（世界坐标/tick/raw yaw/pitch 只在这里出现） -->
          <details class="tm-diag" v-if="round.start_tick != null">
            <summary>技术诊断 / 导出</summary>
            <div class="tm-diag-body">
              <p class="tm-diag-line">地图 {{ map.name }} · 换算 scale={{ map.scale }} · pos=({{ map.pos_x }}, {{ map.pos_y }})</p>
              <p class="tm-diag-line">回合 R{{ round.number }} · start_tick={{ round.start_tick }} · freeze_end_tick={{ round.freeze_end_tick }} · end_tick={{ round.end_tick }} · tick_rate={{ round.tick_rate }}</p>
              <p v-if="!tickRateValid" class="tm-diag-line">⚠ 该回合并未提供可用于核算时长的 tick_rate，时间轴与动态回放已禁用。</p>
              <p v-if="tickRateValid" class="tm-diag-line">道具生命周期（秒/相对 freeze）：</p>
              <ul v-if="tickRateValid" class="tm-diag-list">
                <li v-for="(pr, i) in propItems" :key="i" class="tm-diag-line">
                  {{ pr.type }} · 投掷者={{ pr.thrower_name || pr.thrower_player_id || '归属未知' }} · 有效={{ propWindowText(pr) }} · 世界坐标=({{ pr.x }}, {{ pr.y }}) · 权威={{ pr.authority?.kind || '—' }}
                </li>
              </ul>
              <p v-if="tickRateValid" class="tm-diag-line">击杀播报（真实 player_death，仅诊断）：</p>
              <ul v-if="tickRateValid" class="tm-diag-list">
                <li v-for="(k, i) in killfeedItems" :key="i" class="tm-diag-line">
                  {{ k.time_sec != null ? k.time_sec.toFixed(2) + 's' : '?' }} · {{ (k.attacker_name||'?') }} [{{ k.attacker_team||'?' }}] --{{ k.weapon }}--> {{ (k.victim_name||'?') }} [{{ k.victim_team||'?' }}] · {{ k.class }} · 爆头={{ k.headshot }} · 穿烟={{ k.thrusmoke }} · 穿墙={{ k.penetrated }} · 助攻={{ k.assist_name || '—' }}
                </li>
              </ul>
              <p v-if="tickRateValid" class="tm-diag-line">真实致盲（player_blind，仅诊断）：</p>
              <ul v-if="tickRateValid" class="tm-diag-list">
                <li v-for="(b, i) in blindItems" :key="i" class="tm-diag-line">
                  {{ b.start_sec != null ? b.start_sec.toFixed(2) + 's' : '?' }} · 投掷者={{ b.attacker_name || b.attacker_player_id || '?' }} · 被致盲={{ b.victim_name || b.victim_player_id || '?' }} · {{ b.flash_class }} · 盲时长={{ b.blind_duration }}s · flash_entity={{ b.flash_entity_id }}
                </li>
              </ul>
              <p v-if="tickRateValid" class="tm-diag-line">HE 受伤（thrower+weapon+time-window，authority=inferred，仅诊断）：</p>
              <ul v-if="tickRateValid" class="tm-diag-list">
                <li v-for="(h, i) in heDamageItems" :key="i" class="tm-diag-line">
                  {{ h.time_sec != null ? h.time_sec.toFixed(2) + 's' : '?' }} · 投掷者={{ h.attacker_name || '?' }} · 受伤={{ h.victim_name || '?' }} · 伤害={{ h.dmg_health }} · 关联 he_tick={{ h.he_tick }}
                </li>
              </ul>
              <p v-if="tickRateValid" class="tm-diag-line">射击事件（枪线真值，仅诊断；主界面只画 validated 的瞬时射击线）：</p>
              <ul v-if="tickRateValid" class="tm-diag-list">
                <li v-for="(s, i) in shootItems" :key="i" class="tm-diag-line">
                  {{ s.weapon || '?' }} · 射击@{{ s.time != null ? s.time.toFixed(2) : '?' }}s · 攻击者={{ (players.find(p=>p.player_id===s.player_id)||{}).name || s.player_id }} ·
                  目标={{ (players.find(p=>p.player_id===s.victim_player_id)||{}).name || s.victim_player_id }} ·
                  角误差={{ s.error_deg != null ? s.error_deg + '°' : '?' }} · {{ s.class }} ·
                  权威={{ s.authority || '—' }} · {{ s.validated ? '可用' : '方向不可用' }}
                </li>
              </ul>
              <p v-else class="tm-diag-line">无射击事件或 tick_rate 不可证明。</p>
            </div>
          </details>
        </div>

        <!-- 球员阵容：侧栏（可折叠，默认展开为紧凑列表） -->
        <aside class="tm-roster">
          <details class="tm-roster-layer" open>
            <summary>球员（{{ players.length }}）· 点击突出单玩家</summary>
            <div class="tm-chips" v-if="players.length">
              <button class="tm-chip" :class="{ on: highlight == null }" @click="highlight = null">全部</button>
              <button v-for="p in players" :key="p.player_id" class="tm-chip" :class="{ on: highlight === p.player_id, dead: p.outcome === 'dead' }"
                @click="setHighlight(p.player_id)">{{ p.name }}</button>
            </div>
          </details>
        </aside>
      </div>
    </template>
    <div v-else-if="!loading && !error" class="tm-state">暂无可回放内容</div>
  </div>
</template>

<style scoped>
.tm { display: flex; flex-direction: column; gap: 12px; }
.tm-state { color: var(--text-muted); text-align: center; padding: 28px; font-size: 13px; }
.tm-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.tm-field { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.tm-lbl { font-size: 13px; color: var(--text-secondary); }
.tm-sel { width: auto; min-width: 74px; }
.tm-spacer { flex: 1; }
.tm-seg { display: flex; gap: 4px; flex-shrink: 0; }
.tm-seg-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 5px 11px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; flex-shrink: 0; }
.tm-seg-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.tm-seg-btn.sm { padding: 5px 9px; }
.tm-sum-inline { font-size: 12.5px; color: var(--text-secondary); white-space: nowrap; flex-shrink: 0; }
.tm-sum-inline b { color: var(--text-primary); }
.tm-toggles { display: flex; flex-wrap: wrap; gap: 6px; padding-bottom: 2px; }
.tm-toggle { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 4px 11px; border-radius: 999px; font-size: 12.5px; cursor: pointer; font-weight: 600; }
.tm-toggle.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.tm-toggle:not(.on) { opacity: 0.72; }
.tm-toggle-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.tm-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.tm-chip { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 4px 11px; border-radius: 999px; font-size: 12.5px; cursor: pointer; font-weight: 600; }
.tm-chip.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.tm-chip.dead { opacity: 0.75; }
/* 6R17.2：地图主角布局（桌面主地图占剩余视口约 60–65%） */
.tm-body { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 16px; align-items: start; }
.tm-main { min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.tm-stage { display: flex; flex-direction: column; gap: 10px; }
.tm-canvas { width: 100%; max-width: none; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); aspect-ratio: 1; max-height: 65vh; }
.tm-timeline { display: flex; align-items: center; gap: 12px; }
.tm-play { width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--accent); color: #14100a; font-size: 18px; cursor: pointer; flex: 0 0 auto; }
.tm-play:disabled { opacity: 0.5; cursor: not-allowed; }
.tm-scrub { flex: 1; accent-color: var(--accent); }
.tm-time { font-size: 12.5px; color: var(--text-secondary); min-width: 118px; text-align: right; }
.tm-hint { font-size: 12px; color: var(--text-muted); }
.tm-region { font-size: 12.5px; color: var(--text-secondary); padding: 5px 10px; border-radius: 6px; background: var(--bg-elevated); border: 1px solid var(--border); }
.tm-roster { min-width: 0; }
.tm-roster-layer summary { cursor: pointer; font-size: 13px; color: var(--text-secondary); font-weight: 600; padding: 6px 0; border-bottom: 1px solid var(--border); }
.tm-roster-layer .tm-chips { padding-top: 8px; }
.tm-timeline { display: flex; align-items: center; gap: 12px; width: 100%; max-width: 720px; }
.tm-play { width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--accent); color: #14100a; font-size: 18px; cursor: pointer; flex: 0 0 auto; }
.tm-play:disabled { opacity: 0.5; cursor: not-allowed; }
.tm-scrub { flex: 1; accent-color: var(--accent); }
.tm-time { font-size: 12.5px; color: var(--text-secondary); min-width: 118px; text-align: right; }
.tm-hint { font-size: 12px; color: var(--text-muted); }
.tm-region { width: 100%; max-width: 720px; font-size: 12.5px; color: var(--text-secondary);
  padding: 5px 10px; border-radius: 6px; background: var(--bg-elevated); border: 1px solid var(--border); }
.tm-diag { width: 100%; max-width: 720px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-elevated); padding: 8px 12px; }
.tm-diag summary { cursor: pointer; font-size: 12.5px; color: var(--text-secondary); }
.tm-diag-body { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.tm-diag-line { font-size: 12px; color: var(--text-muted); font-family: ui-monospace, monospace; }
.tm-diag-list { margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 3px; }

@media (max-width: 720px) {
  .tm-toolbar { gap: 8px; }
  .tm-body { grid-template-columns: 1fr; }
  .tm-canvas { max-height: none; }
  /* 6R18：移动端隐藏内联两队摘要避免换行拥挤；保证触控 >=44px；
     图层单行触控横滑（隐藏滚动条 → 无粗重横滚，且不挤压地图/播放首屏） */
  .tm-sum-inline { display: none; }
  .tm-toggles { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
  .tm-toggles::-webkit-scrollbar { display: none; }
  .tm-toggle { min-height: 44px; flex: 0 0 auto; }
  .tm-seg-btn { min-height: 44px; }
  .tm-play { width: 44px; height: 44px; }
}
</style>
