<script setup>
/**
 * 6R8 返工：个人战术回放（pro/max）—— 复用 tactical artifact v2，scope 仅含所选焦点玩家。
 * - 数据源：/api/demos/{id}/personal/replay?round_no=N（仅该玩家轨迹 + 该玩家投掷道具）
 * - 视角朝向：normalize(cos yaw, -sin yaw)（雷达屏幕方向；世界前向 = (cos yaw, sin yaw)；6R12 修正 90° 错误）
 *   （my 朝向开关）；烟/火按 active interval，HE/flash 瞬时（my 道具开关）
 * - 时间原点 = round.freeze_end_tick（00:00），默认跳过 freeze/buy time
 * - 只画所选玩家；道具只画该玩家投掷；切换玩家 → 重新加载，不串缓存
 * - 主界面仅 mm:ss + 中文区域；世界坐标/tick/回合使用明细只进「技术诊断」折叠区
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import RouteHalfReplay from './RouteHalfReplay.vue'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
  playerId: { type: [Number, String], default: null },
  playerName: { type: String, default: '' },
  /** 6R15：来自「长期教练」证据跳转的定位请求 {round, time, layers?, highlight?}；到达后自动 seek 到对应回合/时刻 */
  locateRequest: { type: Object, default: null },
})

const SIZE = 720
const COL = { player: '#f0a020', ball: '#ffffff', spawn: '#3fb950', death: '#f85149', end: '#3fb950',
  gun: '#e8a13c', smoke: 'rgba(150,170,190,0.45)', fire: 'rgba(240,120,40,0.5)',
  he: 'rgba(240,200,60,0.9)', flash: 'rgba(255,255,255,0.85)', traj: 'rgba(125,140,158,0.5)' }
const GUNLEN = 48, GUN_HALF = 12.5, FLASH_SEC = 0.6
const SHOOT_LEN = 62, SHOOT_WINDOW = 0.45   // 瞬时"射击线"长度/时间窗
const KILLFEED_SEC = 4.0      // 击杀播报可见时长（秒）
const HE_HIT_SEC = 1.0        // HE 受伤高亮时长（秒）
const MAX_KILLFEED_ROWS = 6
const BLIND_COLOR = { self: 'rgba(255,255,255,0.9)', team: 'rgba(90,166,255,0.9)', enemy: 'rgba(248,81,73,0.9)' }
const BLIND_LABEL = { self: '自闪', team: '队闪', enemy: '敌闪' }
const PROP_LABEL_COLOR = { smoke: 'rgba(150,170,190,0.9)', fire: 'rgba(240,120,40,0.9)', he: 'rgba(240,200,60,0.9)', flash: 'rgba(255,255,255,0.9)' }

const canvasRef = ref(null)
const radarImg = ref(null)
const data = ref(null)          // 单回合个人战术数据
const rounds = ref([])          // 玩家回合列表
const loading = ref(false)
const error = ref('')
const playing = ref(false)
const currentTime = ref(0)
const selectedRound = ref(1)
const speed = ref(1)
const mode = ref('single')   // single（6R8 战术单回合） | half（多回合半场总览，复用 RouteHalfReplay）
const toggles = reactive({ myProps: true, myLook: true, route: true, name: true, killfeed: true, blind: true })
let rafId = 0, lastTs = 0, loadGeneration = 0
let pendingLocate = null

const reducedMotion = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-reduced-motion: reduce)').matches : false

const round = computed(() => data.value?.round || {})
const mapMeta = computed(() => data.value?.map || {})
const tickRate = computed(() => {
  const r = Number(data.value?.tick_rate); return Number.isFinite(r) && r > 0 ? r : null
})
const tickRateValid = computed(() => tickRate.value != null)
const freezeTick = computed(() => round.value.freeze_end_tick ?? round.value.start_tick ?? 0)
const points = computed(() => data.value?.points || [])
const propItems = computed(() => data.value?.props || [])
const shootItems = computed(() => data.value?.shoots || [])
const killfeedItems = computed(() => data.value?.killfeed || [])
const blindItems = computed(() => data.value?.blinds || [])
const heDamageItems = computed(() => data.value?.he_damage || [])
const maxDuration = computed(() => data.value?.max_duration || 0)
const player = computed(() => data.value?.player || {})
const outcome = computed(() => player.value.outcome || 'unknown')

function fmtTime(sec) {
  if (!Number.isFinite(sec)) return '—'
  const s = Math.max(0, sec); const m = Math.floor(s / 60); const r = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

function drawDot(ctx, rx, ry, r, fill, stroke) { ctx.beginPath(); ctx.arc(rx * SIZE, ry * SIZE, r, 0, Math.PI * 2); ctx.fillStyle = fill; ctx.fill(); if (stroke) { ctx.lineWidth = 2; ctx.strokeStyle = stroke; ctx.stroke() } }
function drawX(ctx, rx, ry, size, color) { ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineCap = 'round'; const x = rx * SIZE, y = ry * SIZE; ctx.beginPath(); ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size); ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size); ctx.stroke() }
function drawPath(ctx, pts, color, width, alpha) { if (pts.length < 2) return; ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.beginPath(); ctx.moveTo(pts[0].rx * SIZE, pts[0].ry * SIZE); for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].rx * SIZE, pts[i].ry * SIZE); ctx.stroke(); ctx.restore() }
function drawLabel(ctx, rx, ry, text, color) { ctx.font = 'bold 11px system-ui, sans-serif'; ctx.textBaseline = 'middle'; const x = rx * SIZE, y = ry * SIZE; const w = ctx.measureText(text).width; ctx.fillStyle = 'rgba(11,15,20,0.85)'; ctx.fillRect(x + 8, y - 8, w + 5, 16); ctx.fillStyle = color; ctx.fillText(text, x + 10, y) }

function lerpYaw(a, b, f) {
  if (a == null && b == null) return null
  if (a == null) return b; if (b == null) return a
  let diff = b - a; while (diff > 180) diff -= 360; while (diff < -180) diff += 360
  return a + diff * f
}

function stateAt(time) {
  const pts = points.value
  if (!pts.length || !tickRateValid.value) return { px: null, py: null, yaw: null }
  const target = freezeTick.value + time * tickRate.value
  if (target <= pts[0].tick) return { px: pts[0].rx, py: pts[0].ry, yaw: pts[0].yaw }
  const last = pts[pts.length - 1]
  if (target >= last.tick) return { px: last.rx, py: last.ry, yaw: last.yaw }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1]
    if (target >= a.tick && target <= b.tick) {
      const f = b.tick === a.tick ? 0 : (target - a.tick) / (b.tick - a.tick)
      return { px: a.rx + (b.rx - a.rx) * f, py: a.ry + (b.ry - a.ry) * f, yaw: lerpYaw(a.yaw, b.yaw, f) }
    }
  }
  return { px: last.rx, py: last.ry, yaw: last.yaw }
}

function drawGunFan(ctx, px, py, yaw) {
  // 6R12 修正：世界前向 = (cos yaw, sin yaw)；雷达/屏幕方向 = (cos yaw, -sin yaw)。勿再用 (-sin,-cos)。
  const rad = yaw * Math.PI / 180
  const theta = Math.atan2(-Math.sin(rad), Math.cos(rad))
  const cx = px * SIZE, cy = py * SIZE, half = GUN_HALF * Math.PI / 180
  ctx.save(); ctx.fillStyle = COL.gun; ctx.globalAlpha = 0.22
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, GUNLEN, theta - half, theta + half); ctx.closePath(); ctx.fill()
  ctx.globalAlpha = 0.9; ctx.lineWidth = 2; ctx.strokeStyle = COL.gun; ctx.lineCap = 'round'
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(theta) * GUNLEN, cy + Math.sin(theta) * GUNLEN); ctx.stroke(); ctx.restore()
}

function propWindow(p) {
  if (p.active_start_sec == null) return null
  if (p.type === 'he' || p.type === 'flash') { return { type: 'instant', t: p.active_start_sec } }
  if (p.active_end_sec == null) return null
  return { type: 'interval', s: p.active_start_sec, e: p.active_end_sec }
}

function drawProps(ctx, t, vis) {
  for (const pr of propItems.value) {
    if (pr.rx == null || pr.ry == null) continue
    if (!toggles.myProps) continue
    const throwerColor = PROP_LABEL_COLOR[pr.type] || '#e7ecf2'
    const w = propWindow(pr)
    if (!w) continue
    if (w.type === 'interval') {
      if (t < w.s || t > w.e) continue
      if (pr.type === 'smoke') { drawDot(ctx, pr.rx, pr.ry, 40, COL.smoke); vis.smoke = true }
      else if (pr.type === 'fire') { drawDot(ctx, pr.rx, pr.ry, 30, COL.fire); vis.fire = true }
      drawThrowerLabel(ctx, pr.rx, pr.ry, pr.thrower_name, throwerColor, pr.type === 'smoke' ? 30 : 44)
    } else if (w.type === 'instant') {
      const dt = t - w.t
      if (dt < 0 || dt > FLASH_SEC) continue
      const frac = dt / FLASH_SEC
      if (pr.type === 'he') { const r = 6 + frac * 26; ctx.save(); ctx.globalAlpha = 1 - frac * 0.6; ctx.strokeStyle = COL.he; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(pr.rx * SIZE, pr.ry * SIZE, r, 0, Math.PI * 2); ctx.stroke(); ctx.restore() }
      else if (pr.type === 'flash') { const r = 8 + frac * 24; ctx.save(); ctx.globalAlpha = 1 - frac * 0.5; const g = ctx.createRadialGradient(pr.rx * SIZE, pr.ry * SIZE, 0, pr.rx * SIZE, pr.ry * SIZE, r); g.addColorStop(0, COL.flash); g.addColorStop(1, 'rgba(255,255,255,0)'); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(pr.rx * SIZE, pr.ry * SIZE, r, 0, Math.PI * 2); ctx.fill(); ctx.restore() }
      drawThrowerLabel(ctx, pr.rx, pr.ry, pr.thrower_name, throwerColor, 14)
    }
  }
}

// 瞬时"射击线"：仅在 validated + 时间窗内渲染（无权威/方向不可用 → 绝不渲染，诚实）
function drawShootLine(ctx, s) {
  const cx = s.rx * SIZE, cy = s.ry * SIZE
  const dx = s.dir_x, dy = s.dir_y
  const ex = cx + dx * SHOOT_LEN, ey = cy + dy * SHOOT_LEN
  ctx.save(); ctx.strokeStyle = '#ffd24a'; ctx.lineWidth = 3; ctx.lineCap = 'round'; ctx.globalAlpha = 0.95
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey); ctx.stroke()
  ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(ex, ey, 4, 0, Math.PI * 2); ctx.stroke(); ctx.restore()
}

// 投掷者短标签（该玩家队色）；个人回放 props 只含所选玩家投掷 → 可直接归属
function drawThrowerLabel(ctx, rx, ry, text, color, dy = 0) {
  if (!text) return
  const x = rx * SIZE, y = ry * SIZE
  ctx.save(); ctx.font = 'bold 11px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  const w = ctx.measureText(text.slice(0, 8)).width
  ctx.fillStyle = 'rgba(11,15,20,0.82)'; ctx.fillRect(x - w / 2 - 4, y + dy - 8, w + 8, 16)
  ctx.fillStyle = color; ctx.textAlign = 'center'; ctx.fillText(text.slice(0, 8), x, y + dy); ctx.textAlign = 'left'
  ctx.restore()
}

// ---- 击杀播报：真实事件时刻出现，约 3-5s 淡出；同刻多杀堆叠不横滚 ----
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
  actives.sort((a, b) => (Number(a.time_sec) - Number(b.time_sec)) || (Number(a.seq) - Number(b.seq)))
  const rows = actives.slice(-MAX_KILLFEED_ROWS)
  const pad = 14, rowH = 19
  const boxX = pad, boxY = pad, boxW = SIZE - pad * 2, boxH = rows.length * rowH + 12
  ctx.save(); ctx.fillStyle = 'rgba(11,15,20,0.62)'; ctx.fillRect(boxX, boxY, boxW, boxH)
  rows.forEach((k, idx) => {
    const st = Number(k.time_sec)
    const frac = Math.min(1, (time - st) / KILLFEED_SEC)
    const alpha = 1 - frac * 0.72
    const ry = boxY + 8 + idx * rowH
    const colA = COL.player
    const colV = '#f85149'
    const atk = k.attacker_name || '未知', vic = k.victim_name || '未知'
    const weapon = (k.weapon || '?').replace(/^weapon_/, '')
    let x = 20
    ctx.globalAlpha = alpha
    ctx.font = 'bold 12px system-ui, sans-serif'; ctx.textBaseline = 'middle'
    ctx.fillStyle = colA; ctx.fillText(atk.slice(0, 10), x, ry); x += ctx.measureText(atk.slice(0, 10)).width + 6
    ctx.fillStyle = '#c9d3df'; ctx.font = '12px system-ui, sans-serif'; ctx.fillText(weapon, x, ry); x += ctx.measureText(weapon).width + 6
    ctx.fillStyle = colV; ctx.font = 'bold 12px system-ui, sans-serif'; ctx.fillText(vic.slice(0, 10), x, ry); x += ctx.measureText(vic.slice(0, 10)).width + 6
    const marks = []
    if (k.headshot) marks.push('爆头')
    if (k.penetrated) marks.push('穿墙')
    if (k.thrusmoke) marks.push('穿烟')
    if (k.assist_name) marks.push('助攻:' + k.assist_name.slice(0, 6))
    if (k.class === 'suicide') marks.push('自杀')
    else if (k.class === 'grenade') marks.push('手雷')
    else if (k.class === 'knife') marks.push('刀杀')
    if (marks.length) { ctx.fillStyle = '#ffd24a'; ctx.font = '11px system-ui, sans-serif'; ctx.fillText(marks.join('·'), x + 14, ry) }
    ctx.globalAlpha = 1
    vis.killfeed = true
  })
  ctx.restore()
}

// ---- 真实致盲：效果只来自 player_blind，绑定受害者当前球，与盲时长对齐 ----
// 个人 scope：只在该玩家为受害者时绘制（位置已知）。
function drawBlind(ctx, time, vis) {
  if (!toggles.blind || !tickRateValid.value) return
  const myId = Number(props.playerId)
  for (const b of blindItems.value) {
    if (b.victim_player_id != null && Number(b.victim_player_id) !== myId) continue
    const s = Number(b.start_sec), e = Number(b.end_sec)
    if (!Number.isFinite(s) || !Number.isFinite(e)) continue
    if (time < s || time > e) continue
    const st = stateAt(time)
    if (st.px == null) continue
    const dur = (e - s) || 1
    const frac = Math.min(1, (time - s) / dur)
    const intensity = 1 - frac
    const color = BLIND_COLOR[b.flash_class] || BLIND_COLOR.self
    const cx = st.px * SIZE, cy = st.py * SIZE, r = 14 + intensity * 8
    ctx.save()
    ctx.globalAlpha = 0.65 + intensity * 0.35
    ctx.strokeStyle = color; ctx.lineWidth = 3.5
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke()
    ctx.globalAlpha = intensity * 0.18; ctx.lineWidth = 7
    ctx.beginPath(); ctx.arc(cx, cy, r + 4, 0, Math.PI * 2); ctx.stroke()
    ctx.globalAlpha = intensity * 0.45; ctx.fillStyle = color
    ctx.beginPath(); ctx.arc(cx, cy, r - 2, 0, Math.PI * 2); ctx.fill()
    ctx.globalAlpha = 0.95; ctx.font = 'bold 12px system-ui, sans-serif'
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    const label = b.flash_class ? (BLIND_LABEL[b.flash_class] || '闪') : '闪'
    const txt = `${label} ${(e - time).toFixed(1)}s`
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

// ---- HE 受伤：瞬时爆点只是原因，高亮真实受伤（仅该玩家为受害者） ----
function drawHEDamage(ctx, time, vis) {
  if (!toggles.myProps || !tickRateValid.value) return
  const myId = Number(props.playerId)
  for (const h of heDamageItems.value) {
    if (h.victim_player_id != null && Number(h.victim_player_id) !== myId) continue
    const st = Number(h.time_sec)
    if (!Number.isFinite(st)) continue
    if (time < st || time > st + HE_HIT_SEC) continue
    const pos = stateAt(time)
    if (pos.px == null) continue
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

function render() {
  const canvas = canvasRef.value; if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, SIZE, SIZE)
  if (radarImg.value) ctx.drawImage(radarImg.value, 0, 0, SIZE, SIZE)
  const t = currentTime.value
  const vis = { smoke: false, fire: false, gun: false, shoot: false, blind: false, killfeed: false, hedamage: false }

  if (tickRateValid.value && toggles.myProps) drawProps(ctx, t, vis)
  else if (toggles.myProps) { /* tickRate 不可证明 → 不画（诚实） */ }

  // 瞬时"射击线"（仅 validated；方向不可用 → 不渲染）
  if (tickRateValid.value && toggles.myLook) {
    for (const s of shootItems.value) {
      if (!s || !s.validated || !(s.rx != null && s.ry != null && s.dir_x != null && s.dir_y != null)) continue
      const st = Number(s.time)
      if (!Number.isFinite(st)) continue
      if (Math.abs(t - st) <= SHOOT_WINDOW) { drawShootLine(ctx, s); vis.shoot = true }
    }
  }

  const pts = points.value
  if (pts.length && !tickRateValid.value) {
    // 时长不可核算：完整路线 + 终态（不画当前球/枪线）
    if (toggles.route) drawPath(ctx, pts, COL.player, 2.2, 0.62)
  } else if (pts.length) {
    const st = stateAt(t)
    const cTick = freezeTick.value + t * tickRate.value
    if (toggles.route) {
      const explored = pts.filter((p) => p.tick <= cTick)
      if (explored.length) {
        drawPath(ctx, explored, COL.player, 2.2, 0.62)
        if (explored[explored.length - 1].tick < cTick && st.px != null) {
          ctx.save(); ctx.globalAlpha = 0.62; ctx.strokeStyle = COL.player; ctx.lineWidth = 2.2
          ctx.beginPath(); ctx.moveTo(explored[explored.length - 1].rx * SIZE, explored[explored.length - 1].ry * SIZE)
          ctx.lineTo(st.px * SIZE, st.py * SIZE); ctx.stroke(); ctx.restore()
        }
      }
    }
    // 存活终局
    if (outcome.value === 'alive' && round.value.end_tick <= cTick && pts.length) {
      const last = pts[pts.length - 1]; ctx.fillStyle = COL.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
    }
    // 死亡 X（仅阵亡且到达死亡（alive flip 处））
    const deathPoint = pts.find((p) => p.alive === false)
    const currentGone = outcome.value === 'dead' && deathPoint != null && cTick >= deathPoint.tick
    if (currentGone) { ctx.save(); drawX(ctx, deathPoint.rx, deathPoint.ry, 7, COL.death); ctx.restore() }
    // 当前球 + 枪线（存活/未知才显示；阵亡到达死亡 tick 后移除当前球）
    if (st.px != null && !currentGone) {
      if (toggles.myLook && st.yaw != null) { drawGunFan(ctx, st.px, st.py, st.yaw); vis.gun = true }
      drawDot(ctx, st.px, st.py, 6, COL.ball, COL.player)
      if (toggles.name) drawLabel(ctx, st.px, st.py, `${(props.playerName || player.value.name || '我').slice(0, 4)}·${player.value.side || ''}`, COL.player)
    }
  }

  // 6R13：只在该玩家为受害者时绘制致盲/HE 高亮；击杀播报
  if (tickRateValid.value) {
    drawBlind(ctx, t, vis)
    drawHEDamage(ctx, t, vis)
    drawKillfeed(ctx, t, vis)
  }

  // 图例
  const items = [
    { c: COL.player, shape: 'dot', label: '我的路线' },
    { c: COL.ball, shape: 'ball', label: '当前' },
    { c: COL.death, shape: 'x', label: '阵亡' },
  ]
  if (toggles.myLook) { items.push({ c: COL.gun, shape: 'gun', label: '我的视角朝向' }); items.push({ c: '#ffd24a', shape: 'shoot', label: '射击线（已验证）' }) }
  if (toggles.myProps) { items.push({ c: COL.smoke, shape: 'smoke', label: '烟' }); items.push({ c: COL.fire, shape: 'fire', label: '火' }); items.push({ c: COL.he, shape: 'he', label: '手雷' }); items.push({ c: COL.flash, shape: 'flash', label: '闪' }) }
  if (toggles.blind) items.push({ c: BLIND_COLOR.enemy, shape: 'blind', label: '致盲（自/队/敌）' })
  if (toggles.killfeed) items.push({ c: '#ffd24a', shape: 'killfeed', label: '击杀播报' })
  const pad = 14; const boxW = 210; const boxH = items.length * 17 + 16
  const bx = pad, by = SIZE - boxH - pad
  ctx.fillStyle = 'rgba(11,15,20,0.72)'; ctx.fillRect(bx, by, boxW, boxH)
  ctx.font = '11px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  items.forEach((it, i) => {
    const ly = by + 14 + i * 17, lx = bx + 14
    if (it.shape === 'dot') drawDot(ctx, lx / SIZE, ly / SIZE, 3.5, it.c)
    else if (it.shape === 'x') drawX(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'ball') drawDot(ctx, lx / SIZE, ly / SIZE, 5, it.c, COL.player)
    else if (it.shape === 'gun') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(lx - 5, ly + 4); ctx.lineTo(lx + 5, ly - 4); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'shoot') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(lx - 6, ly + 5); ctx.lineTo(lx + 6, ly - 5); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'smoke' || it.shape === 'fire') { ctx.save(); ctx.globalAlpha = 0.5; ctx.fillStyle = it.c; ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI * 2); ctx.fill(); ctx.restore() }
    else if (it.shape === 'he') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'flash') { ctx.save(); ctx.globalAlpha = 0.7; ctx.fillStyle = it.c; ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.fill(); ctx.restore() }
    else if (it.shape === 'blind') { ctx.save(); ctx.strokeStyle = it.c; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI * 2); ctx.stroke(); ctx.restore() }
    else if (it.shape === 'killfeed') { ctx.save(); ctx.fillStyle = it.c; ctx.fillRect(lx - 5, ly - 3, 10, 6); ctx.restore() }
    ctx.fillStyle = '#e8edf4'; ctx.fillText(it.label, lx + 12, ly)
  })

  canvas.setAttribute('data-ready', 'ready')
  canvas.setAttribute('data-gun', vis.gun ? '1' : '')
  canvas.setAttribute('data-shoot', vis.shoot ? '1' : '')
  canvas.setAttribute('data-smoke', vis.smoke ? '1' : '')
  canvas.setAttribute('data-fire', vis.fire ? '1' : '')
  canvas.setAttribute('data-blind', vis.blind ? '1' : '')
  canvas.setAttribute('data-killfeed', vis.killfeed ? '1' : '')
  canvas.setAttribute('data-hedamage', vis.hedamage ? '1' : '')
  canvas.setAttribute('data-time', String(Math.round(t * 100) / 100))
}

let renderPending = false
function scheduleRender(retries = 0) {
  if (renderPending) return; renderPending = true
  nextTick(() => { renderPending = false; if (canvasRef.value) { render(); return } if (retries < 40) scheduleRender(retries + 1) })
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
function play() { if (reducedMotion || !maxDuration.value) return; if (currentTime.value >= maxDuration.value) currentTime.value = 0; playing.value = true; lastTs = 0; rafId = requestAnimationFrame(tick) }
function pause() { playing.value = false; if (rafId) cancelAnimationFrame(rafId) }
function togglePlay() { playing.value ? pause() : play() }
function onScrub(e) { pause(); currentTime.value = Math.min(Number(e.target.value), maxDuration.value); render() }
function setSpeed(v) { speed.value = v }
function setMode(m) { mode.value = m; resetPlayback() }
function resetPlayback() { pause(); currentTime.value = 0; render() }

// 当前区域（该玩家此刻）
const currentPlace = computed(() => {
  if (!tickRateValid.value || !points.value.length) return '未知区域'
  const tick = freezeTick.value + currentTime.value * tickRate.value
  let best = points.value[0], bd = Infinity
  for (const p of points.value) { const d = Math.abs((p.tick ?? 0) - tick); if (d < bd) { bd = d; best = p } }
  return best.place || '未知区域'
})

async function loadRadar() {
  const img = new Image(); img.crossOrigin = 'anonymous'; img.src = `/api/maps/${props.mapName}/radar`
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej })
  radarImg.value = img; scheduleRender()
}
async function loadRound(roundNo) {
  const gen = ++loadGeneration
  loading.value = true; error.value = ''
  try {
    const res = await api.get(`/api/demos/${props.demoId}/personal/replay?round_no=${roundNo}`)
    if (gen !== loadGeneration) return
    data.value = res; resetPlayback()
    // 若由外部定位而来（如长期教练证据跳转），应用目标时刻 + 图层
    if (pendingLocate && pendingLocate.round === Number(roundNo)) {
      const pl = pendingLocate; pendingLocate = null
      applyLocateLayers(pl)
      if (tickRateValid.value && maxDuration.value > 0) {
        currentTime.value = Math.min(Math.max(0, pl.time ?? 0), maxDuration.value)
      }
      scheduleRender()
    }
  } catch (e) {
    if (gen !== loadGeneration) return
    error.value = e instanceof ApiError ? e.message : '个人战术回放加载失败'
    data.value = null
  } finally { if (gen === loadGeneration) { loading.value = false; scheduleRender() } }
}
async function loadRounds() {
  const gen = ++loadGeneration
  loading.value = true; error.value = ''
  try {
    const res = await api.get(`/api/demos/${props.demoId}/personal/replay`)
    if (gen !== loadGeneration) return
    rounds.value = res.rounds || []
    let r = rounds.value[0]?.round || null
    if (r != null) await loadRound(r)
    else { data.value = null; resetPlayback() }
    // 6R15：外部定位请求（长期教练证据跳转）在数据就绪后再落地
    if (props.locateRequest?.round != null) {
      locate(props.locateRequest.round, props.locateRequest.time, { layers: props.locateRequest.layers, highlight: props.locateRequest.highlight })
    }
  } catch (e) {
    if (gen !== loadGeneration) return
    error.value = e instanceof ApiError ? e.message : '个人战术回放加载失败'
    rounds.value = []; data.value = null
  } finally { if (gen === loadGeneration) { loading.value = false; scheduleRender() } }
}
function selectRound(r) { selectedRound.value = Number(r); loadRound(selectedRound.value) }

// ---- 6R15：对外定位（长期教练证据跳转）——镜像 TeamSyncReplay.locate ----
// 图层键映射：证据层的 'gun' 在个人回放里对应「我的视角朝向」(myLook)。
const LAYER_MAP = { route: 'route', gun: 'myLook' }
function applyLocateLayers(pl) {
  for (const l of pl.layers || []) {
    const k = LAYER_MAP[l] || l
    if (k in toggles) toggles[k] = true
  }
}
function applyLocate(req) {
  pendingLocate = null
  applyLocateLayers(req)
  pause()
  currentTime.value = tickRateValid.value && maxDuration.value > 0
    ? Math.min(Math.max(0, req.time ?? 0), maxDuration.value)
    : 0
  scheduleRender()
}
function locate(round, time, opts = {}) {
  pendingLocate = {
    round: Number(round),
    time: time == null ? null : Number(time),
    layers: Array.isArray(opts?.layers) ? opts.layers : [],
    highlight: Array.isArray(opts?.highlight) ? opts.highlight : [],
  }
  if (Number(round) !== selectedRound.value || !data.value) {
    selectedRound.value = Number(round)
    loadRound(selectedRound.value)
  } else {
    applyLocate(pendingLocate)
  }
}
defineExpose({ locate })

function reloadForPlayer() {
  // 切换玩家 → 重置并重新加载，绝不跨玩家复用缓存/状态
  pause()
  currentTime.value = 0
  selectedRound.value = 1
  data.value = null
  if (props.playerId != null) loadRounds()
}

const canProceed = computed(() => props.playerId != null)
async function init() { if (canProceed.value) { try { await loadRadar() } catch { /* ignore */ } loadRounds() } }

watch(() => props.playerId, () => reloadForPlayer())
watch(() => props.locateRequest, (req) => {
  // 外部定位请求在数据已就绪时即时落地（如 Report 内再次切换证据）
  if (req?.round != null && data.value) {
    locate(req.round, req.time, { layers: req.layers, highlight: req.highlight })
  }
})
watch([radarImg, points, propItems, shootItems, killfeedItems, blindItems, heDamageItems, toggles, playing], () => scheduleRender())
watch(() => props.mapName, () => { if (canProceed.value) { try { loadRadar() } catch { /* ignore */ } } })

onMounted(init)
onBeforeUnmount(() => { pause() })
</script>

<template>
  <div class="pt" :data-player="player.player_id">
    <!-- 模式：单回合战术回放（6R8） / 半场多回合总览（复用 RouteHalfReplay） -->
    <div class="pt-mode" role="group" aria-label="回放模式">
      <button class="pt-mode-btn" :class="{ on: mode === 'single' }" @click="setMode('single')">单回合战术回放</button>
      <button class="pt-mode-btn" :class="{ on: mode === 'half' }" @click="setMode('half')">半场总览</button>
    </div>

    <template v-if="mode === 'single'">
    <div v-if="loading" class="pt-state">加载个人战术回放…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else-if="data">
      <div class="pt-controls">
        <label class="pt-field"><span class="pt-lbl">回合</span>
          <select class="input pt-sel" :value="round.number" @change="selectRound($event.target.value)">
            <option v-for="r in rounds" :key="r.round" :value="r.round">
              R{{ r.round }} · {{ r.side || '—' }}{{ r.props ? ` · 道具${r.props}` : '' }}{{ r.outcome === 'dead' ? ' · 阵亡' : '' }}
            </option>
          </select>
        </label>
        <div class="pt-spacer"></div>
        <div class="pt-seg" role="group" aria-label="倍速">
          <button v-for="v in [0.5, 1, 2]" :key="v" class="pt-seg-btn sm" :class="{ on: speed === v }" @click="setSpeed(v)">{{ v }}x</button>
        </div>
      </div>

      <!-- 个人图层开关：我的道具 / 我的朝向（必需） -->
      <div class="pt-toggles" role="group" aria-label="个人图层开关">
        <button class="pt-toggle" :class="{ on: toggles.myLook }" :aria-pressed="toggles.myLook" @click="toggles.myLook = !toggles.myLook">
          <span class="pt-dot" style="background:#e8a13c"></span>我的视角朝向
        </button>
        <button class="pt-toggle" :class="{ on: toggles.myProps }" :aria-pressed="toggles.myProps" @click="toggles.myProps = !toggles.myProps">
          <span class="pt-dot" style="background:#96aabe"></span>我的道具（烟/火/闪/雷）
        </button>
        <button class="pt-toggle" :class="{ on: toggles.route }" :aria-pressed="toggles.route" @click="toggles.route = !toggles.route">
          <span class="pt-dot" style="background:#f0a020"></span>路线
        </button>
        <button class="pt-toggle" :class="{ on: toggles.name }" :aria-pressed="toggles.name" @click="toggles.name = !toggles.name">
          <span class="pt-dot" style="background:#e7ecf2"></span>玩家名
        </button>
        <button class="pt-toggle" :class="{ on: toggles.killfeed }" :aria-pressed="toggles.killfeed" @click="toggles.killfeed = !toggles.killfeed">
          <span class="pt-dot" style="background:#ffd24a"></span>击杀播报
        </button>
        <button class="pt-toggle" :class="{ on: toggles.blind }" :aria-pressed="toggles.blind" @click="toggles.blind = !toggles.blind">
          <span class="pt-dot" style="background:#f85149"></span>真实致盲
        </button>
      </div>

      <div class="pt-stage">
        <canvas ref="canvasRef" :width="SIZE" :height="SIZE" class="pt-canvas" data-ready="ready"></canvas>
        <div v-if="currentPlace" class="pt-region">当前区域：{{ currentPlace }}</div>
        <div class="pt-timeline">
          <button class="pt-play" :disabled="reducedMotion || !tickRateValid" @click="togglePlay" :aria-label="playing ? '暂停' : '播放'">{{ reducedMotion ? '⏵' : (playing ? '⏸' : '⏵') }}</button>
          <input class="pt-scrub" type="range" min="0" :max="tickRateValid ? maxDuration : 0" step="0.01" :value="currentTime" @input="onScrub" :disabled="!tickRateValid" aria-label="时间轴" />
          <span class="pt-time num" v-if="tickRateValid">当前 {{ fmtTime(currentTime) }} / {{ fmtTime(maxDuration) }}</span>
          <span class="pt-time num" v-else>时长不可核算</span>
        </div>
        <p v-if="reducedMotion" class="pt-hint">已开启减弱动效：拖动时间轴查看，不自动播放。</p>
        <p v-if="!tickRateValid" class="pt-hint">后端未提供可证明的 tick_rate，本回合时长无法核算，仅展示静态。</p>
        <p class="pt-hint">仅显示所选玩家（{{ player.name }}）；道具只统计该玩家投掷；视角朝向为连续插值眼朝向，非移动方向；瞬时"射击线"仅在通过真值门禁时显示；击杀播报来自真实 player_death；致盲来自 player_blind（盲时长真实）；HE 受伤为 thrower+weapon+time-window 关联（derived）。</p>

        <!-- 技术诊断 / 导出：坐标、tick、道具窗口与本玩家回合使用明细 -->
        <details class="pt-diag" v-if="round.start_tick != null">
          <summary>技术诊断 / 导出</summary>
          <div class="pt-diag-body">
            <p class="pt-diag-line">地图 {{ mapMeta.name }} · scale={{ mapMeta.scale }} · pos=({{ mapMeta.pos_x }}, {{ mapMeta.pos_y }})</p>
            <p class="pt-diag-line">回合 R{{ round.number }} · start_tick={{ round.start_tick }} · freeze_end_tick={{ round.freeze_end_tick }} · end_tick={{ round.end_tick }} · tick_rate={{ round.tick_rate }}</p>
            <p v-if="tickRateValid" class="pt-diag-line">道具（秒/相对 freeze，仅该玩家投掷）：</p>
            <ul v-if="tickRateValid" class="pt-diag-list">
              <li v-for="(pr, i) in propItems" :key="i" class="pt-diag-line">
                {{ pr.type }} · 投掷者={{ pr.thrower_name }} · 有效={{ pr.active_start_sec ?? '?' }}–{{ pr.active_end_sec ?? '未证明' }} · 世界坐标=({{ pr.x }}, {{ pr.y }})
              </li>
            </ul>
            <p v-if="tickRateValid" class="pt-diag-line">击杀播报（真实 player_death，仅该玩家相关）：</p>
            <ul v-if="tickRateValid" class="pt-diag-list">
              <li v-for="(k, i) in killfeedItems" :key="i" class="pt-diag-line">
                {{ k.time_sec != null ? k.time_sec.toFixed(2) + 's' : '?' }} · {{ k.attacker_name||'?' }} --{{ k.weapon }}--> {{ k.victim_name||'?' }} · {{ k.class }} · 爆头={{ k.headshot }} · 穿烟={{ k.thrusmoke }} · 穿墙={{ k.penetrated }} · 助攻={{ k.assist_name || '—' }}
              </li>
            </ul>
            <p v-if="tickRateValid" class="pt-diag-line">真实致盲（player_blind，仅该玩家相关）：</p>
            <ul v-if="tickRateValid" class="pt-diag-list">
              <li v-for="(b, i) in blindItems" :key="i" class="pt-diag-line">
                {{ b.start_sec != null ? b.start_sec.toFixed(2) + 's' : '?' }} · 投掷者={{ b.attacker_name || '?' }} · 被致盲={{ b.victim_name || '?' }} · {{ b.flash_class }} · 盲时长={{ b.blind_duration }}s
              </li>
            </ul>
            <p v-if="tickRateValid" class="pt-diag-line">HE 受伤（authority=inferred，仅该玩家相关）：</p>
            <ul v-if="tickRateValid" class="pt-diag-list">
              <li v-for="(h, i) in heDamageItems" :key="i" class="pt-diag-line">
                {{ h.time_sec != null ? h.time_sec.toFixed(2) + 's' : '?' }} · 投掷者={{ h.attacker_name || '?' }} · 受伤={{ h.victim_name || '?' }} · 伤害={{ h.dmg_health }} · 关联 he_tick={{ h.he_tick }}
              </li>
            </ul>
            <p v-if="tickRateValid" class="pt-diag-line">射击事件（枪线真值，仅诊断）：</p>
            <ul v-if="tickRateValid" class="pt-diag-list">
              <li v-for="(s, i) in shootItems" :key="i" class="pt-diag-line">
                {{ s.weapon || '?' }} · 射击@{{ s.time != null ? s.time.toFixed(2) : '?' }}s ·
                目标={{ s.victim_player_id || '—' }} · 角误差={{ s.error_deg != null ? s.error_deg + '°' : '?' }} ·
                {{ s.class }} · {{ s.validated ? '可用' : '方向不可用' }}
              </li>
            </ul>
            <!-- 回合使用明细（降级到技术诊断） -->
            <p class="pt-diag-line">回合使用明细：</p>
            <ul class="pt-diag-list">
              <li v-for="r in rounds" :key="r.round" class="pt-diag-line">R{{ r.round }} · {{ r.side || '—' }} · {{ r.outcome }} · 道具 {{ r.props }}</li>
            </ul>
          </div>
        </details>
      </div>
    </template>
    <div v-else-if="!loading && !error" class="pt-state">该玩家暂无可回放的战术数据</div>
    </template>
    <!-- 半场总览 -->
    <RouteHalfReplay v-else :demo-id="demoId" :map-name="mapName" :player-id="playerId" :player-name="playerName" />
  </div>
</template>

<style scoped>
.pt { display: flex; flex-direction: column; gap: 12px; }
.pt-mode { display: flex; gap: 6px; }
.pt-mode-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary);
  padding: 5px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.pt-mode-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.pt-state { color: var(--text-muted); text-align: center; padding: 28px; font-size: 13px; }
.pt-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.pt-field { display: flex; align-items: center; gap: 8px; }
.pt-lbl { font-size: 13px; color: var(--text-secondary); }
.pt-sel { width: auto; min-width: 150px; }
.pt-spacer { flex: 1; }
.pt-seg { display: flex; gap: 4px; }
.pt-seg-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 5px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.pt-seg-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.pt-seg-btn.sm { padding: 5px 9px; }
.pt-toggles { display: flex; flex-wrap: wrap; gap: 6px; }
.pt-toggle { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 4px 11px; border-radius: 999px; font-size: 12.5px; cursor: pointer; font-weight: 600; }
.pt-toggle.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.pt-toggle:not(.on) { opacity: 0.72; }
.pt-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.pt-stage { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.pt-canvas { width: 100%; max-width: 720px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); aspect-ratio: 1; }
.pt-timeline { display: flex; align-items: center; gap: 12px; width: 100%; max-width: 720px; }
.pt-play { width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--accent); color: #14100a; font-size: 18px; cursor: pointer; flex: 0 0 auto; }
.pt-play:disabled { opacity: 0.5; cursor: not-allowed; }
.pt-scrub { flex: 1; accent-color: var(--accent); }
.pt-time { font-size: 12.5px; color: var(--text-secondary); min-width: 118px; text-align: right; }
.pt-hint { font-size: 12px; color: var(--text-muted); }
.pt-region { width: 100%; max-width: 720px; font-size: 12.5px; color: var(--text-secondary); padding: 5px 10px; border-radius: 6px; background: var(--bg-elevated); border: 1px solid var(--border); }
.pt-diag { width: 100%; max-width: 720px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated); padding: 8px 12px; }
.pt-diag summary { cursor: pointer; font-size: 12.5px; color: var(--text-secondary); }
.pt-diag-body { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.pt-diag-line { font-size: 12px; color: var(--text-muted); font-family: ui-monospace, monospace; }
.pt-diag-list { margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 3px; }
@media (max-width: 720px) { .pt-controls { gap: 8px; } .pt-field { flex: 1 1 100%; } }
</style>
