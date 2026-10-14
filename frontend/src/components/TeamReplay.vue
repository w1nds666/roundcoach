<script setup>
/**
 * 6R6A：团队十人单回合同步路线回放（Max）。
 * - 雷达 Canvas 同步播放该回合最多 10 名玩家；时间从该回合 00:00 对齐
 * - 稳定队伍色：队伍 A 蓝 / 队伍 B 橙；标签/图例同时显示本回合真实 CT/T（不混淆身份与阵营）
 * - 每球显示短名/编号；焦点玩家（“我”）白色外环
 * - 控件：回合选择、播放/暂停、拖动、0.5/1/2x；筛选 全部/队伍A/队伍B；点击玩家 chip 仅突出该玩家
 * - 默认不显示未来路径；kill/death X/存活绿终点只在到达时出现；死亡玩家停在 X
 * - 诚实摘要：A/B 人数、存活、击杀、胜方（不编造配合评分/地图控制/补枪率）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import { routeReplayState } from '../stores/routeReplay'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
  maxRound: { type: Number, default: 0 },
})

const SIZE = 720
const TEAM_COLOR = { A: '#58a6ff', B: '#f0a020' }   // 稳定队伍色（长期一致）
const MARK = {
  spawn: '#3fb950', death: '#f85149', end: '#3fb950', ball: '#ffffff',
  focusRing: '#ffffff', kill: '#f5c518',
}
const ROUTE_ALPHA = 0.62
const FADED_ALPHA = 0.14

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
let rafId = 0, lastTs = 0, loadGeneration = 0

const speed = computed({ get: () => routeReplayState.speed, set: (v) => { routeReplayState.speed = v } })
const reducedMotion = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-reduced-motion: reduce)').matches : false

const round = computed(() => team.value?.round || {})
const map = computed(() => team.value?.map || {})
const players = computed(() => team.value?.players || [])
const maxDuration = computed(() => team.value?.max_duration || 0)
const focusPlayerId = computed(() => team.value?.focus_player_id)
const winner = computed(() => team.value?.winner)
// 只信任后端提供的 tick_rate：为正数才核算秒数；缺失/为 0/不可证明时绝不伪造，标记为「时长不可核算」。
const tickRate = computed(() => {
  const r = Number(team.value?.tick_rate)
  return Number.isFinite(r) && r > 0 ? r : null
})
const tickRateValid = computed(() => tickRate.value != null)

const summary = computed(() => {
  const ps = players.value
  const a = ps.filter((p) => p.team_identity === 'A')
  const b = ps.filter((p) => p.team_identity === 'B')
  const alive = ps.filter((p) => p.outcome === 'alive').length
  const kills = ps.reduce((n, p) => n + (p.events?.kills?.length || 0), 0)
  return { a: a.length, b: b.length, alive, kills }
})

function fmtTime(sec) { const s = Math.max(0, sec); const m = Math.floor(s / 60); const r = Math.floor(s % 60); return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` }

function drawDot(ctx, rx, ry, r, fill, stroke) { ctx.beginPath(); ctx.arc(rx * SIZE, ry * SIZE, r, 0, Math.PI * 2); ctx.fillStyle = fill; ctx.fill(); if (stroke) { ctx.lineWidth = 2; ctx.strokeStyle = stroke; ctx.stroke() } }
function drawX(ctx, rx, ry, size, color) { ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineCap = 'round'; const x = rx * SIZE, y = ry * SIZE; ctx.beginPath(); ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size); ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size); ctx.stroke() }
function drawDiamond(ctx, rx, ry, size, color) { const x = rx * SIZE, y = ry * SIZE; ctx.beginPath(); ctx.moveTo(x, y - size); ctx.lineTo(x + size, y); ctx.lineTo(x, y + size); ctx.lineTo(x - size, y); ctx.closePath(); ctx.fillStyle = color; ctx.fill() }
function drawPath(ctx, pts, color, width, alpha) { if (pts.length < 2) return; ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.beginPath(); ctx.moveTo(pts[0].rx * SIZE, pts[0].ry * SIZE); for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].rx * SIZE, pts[i].ry * SIZE); ctx.stroke(); ctx.restore() }
function drawLabel(ctx, rx, ry, text, color) { ctx.font = 'bold 11px system-ui, sans-serif'; ctx.textBaseline = 'middle'; const x = rx * SIZE, y = ry * SIZE; const w = ctx.measureText(text).width; ctx.fillStyle = 'rgba(11,15,20,0.85)'; ctx.fillRect(x + 8, y - 8, w + 5, 16); ctx.fillStyle = color; ctx.fillText(text, x + 10, y) }

function ballAt(player, time) {
  const pts = player.points
  if (!pts.length || !tickRateValid.value) return { px: null, py: null }
  const tick = round.value.start_tick + time * tickRate.value
  if (tick <= pts[0].tick) return { px: pts[0].rx, py: pts[0].ry }
  const last = pts[pts.length - 1]
  if (tick >= last.tick) return { px: last.rx, py: last.ry }
  for (let i = 0; i < pts.length - 1; i++) { const a = pts[i], b = pts[i + 1]; if (tick >= a.tick && tick <= b.tick) { const f = b.tick === a.tick ? 0 : (tick - a.tick) / (b.tick - a.tick); return { px: a.rx + (b.rx - a.rx) * f, py: a.ry + (b.ry - a.ry) * f } } }
  return { px: last.rx, py: last.ry }
}

function playerDeathTick(player) {
  const de = player.events?.death
  if (de) return de.tick
  for (const p of (player.points || [])) if ((p.events || []).includes('death')) return p.tick
  return null
}

// 优先级：焦点玩家 > 突出玩家 > 各可见玩家区域一致时显示；否则省略
const currentPlace = computed(() => {
  if (!tickRateValid.value) return null
  const ps = players.value
  if (!ps.length) return null
  const target = focusPlayerId.value != null ? ps.find((p) => p.player_id === focusPlayerId.value)
    : (highlight.value != null ? ps.find((p) => p.player_id === highlight.value) : null)
  if (target && target.points?.length) {
    const tick = round.value.start_tick + currentTime.value * tickRate.value
    let best = target.points[0]; let bestD = Infinity
    for (const p of target.points) { const d = Math.abs((p.tick ?? 0) - tick); if (d < bestD) { bestD = d; best = p } }
    return best.place || '未知区域'
  }
  if (focusPlayerId.value == null && highlight.value == null) {
    // 全部可见：若各点一致才给一个区域，否则不误导
    const shown = ps.filter((p) => filter.value === 'all' || p.team_identity === filter.value)
    const places = new Set()
    const tick = round.value.start_tick + currentTime.value * tickRate.value
    for (const p of shown) {
      if (!p.points?.length) continue
      let best = p.points[0]; let bestD = Infinity
      for (const pt of p.points) { const d = Math.abs((pt.tick ?? 0) - tick); if (d < bestD) { bestD = d; best = pt } }
      places.add(best.place || '未知区域')
    }
    if (places.size === 1) return [...places][0]
    return null
  }
  return null
})

function drawPlayer(ctx, player, teamColor, alpha, isFocus, staticMode) {
  const pts = player.points
  if (!pts.length) return

  // 无法核算时长：完整路线 + 全部事件标记（不含当前球/标签），不伪造时间
  if (staticMode) {
    drawPath(ctx, pts, teamColor, 2.2, alpha)
    ctx.save(); ctx.globalAlpha = alpha
    for (const p of pts) for (const e of (p.events || [])) {
      if (e === 'spawn') drawDot(ctx, p.rx, p.ry, 3.5, MARK.spawn)
      else if (e === 'kill') drawDiamond(ctx, p.rx, p.ry, 5, MARK.kill)
      else if (e === 'death') drawX(ctx, p.rx, p.ry, 7, MARK.death)
    }
    if (player.outcome === 'alive' && pts.length) {
      const last = pts[pts.length - 1]; ctx.fillStyle = MARK.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
    }
    ctx.restore()
    return
  }

  const cTick = round.value.start_tick + currentTime.value * tickRate.value
  const ball = ballAt(player, currentTime.value)

  if (ball.px !== null) {
    const explored = pts.filter((p) => p.tick <= cTick)
    drawPath(ctx, explored.length ? explored : [pts[0]], teamColor, 2.2, alpha)
    if (explored.length && explored[explored.length - 1].tick < cTick) {
      ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = teamColor; ctx.lineWidth = 2.2
      ctx.beginPath(); ctx.moveTo(explored[explored.length - 1].rx * SIZE, explored[explored.length - 1].ry * SIZE); ctx.lineTo(ball.px * SIZE, ball.py * SIZE); ctx.stroke(); ctx.restore()
    }
  }

  ctx.save(); ctx.globalAlpha = alpha
  for (const p of pts) for (const e of (p.events || [])) {
    if (e === 'spawn') drawDot(ctx, p.rx, p.ry, 3.5, MARK.spawn)
    else if (e === 'kill' && p.tick <= cTick) drawDiamond(ctx, p.rx, p.ry, 5, MARK.kill)
    else if (e === 'death' && p.tick <= cTick) drawX(ctx, p.rx, p.ry, 7, MARK.death)
  }
  if (player.outcome === 'alive' && round.value.end_tick <= cTick && pts.length) {
    const last = pts[pts.length - 1]; ctx.fillStyle = MARK.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
  }
  // 死亡玩家：到达死亡 tick 后移除「当前」球，只留唯一 X（不叠加）。
  const deadTick = playerDeathTick(player)
  const currentGone = player.outcome === 'dead' && deadTick != null && cTick >= deadTick
  if (ball.px !== null && !currentGone) {
    // 球 + 焦点白环 + 短名/编号 + 本回合真实阵营
    drawDot(ctx, ball.px, ball.py, 6, MARK.ball, teamColor)
    if (isFocus) { ctx.lineWidth = 2.5; ctx.strokeStyle = MARK.focusRing; ctx.beginPath(); ctx.arc(ball.px * SIZE, ball.py * SIZE, 9, 0, Math.PI * 2); ctx.stroke() }
    drawLabel(ctx, ball.px, ball.py, `${(player.name || 'P' + player.player_id).slice(0, 3)}·${player.side || '—'}`, teamColor)
  }
  ctx.restore()
}

function drawLegend(ctx) {
  const items = [
    { c: TEAM_COLOR.A, shape: 'dot', label: '队伍 A' },
    { c: TEAM_COLOR.B, shape: 'dot', label: '队伍 B' },
    { c: MARK.spawn, shape: 'dot', label: '出生' },
    { c: MARK.kill, shape: 'diamond', label: '击杀' },
    { c: MARK.death, shape: 'x', label: '死亡' },
    { c: MARK.ball, shape: 'ball', label: '当前' },
    { c: MARK.end, shape: 'sq', label: '存活终局' },
    { c: MARK.focusRing, shape: 'ring', label: '焦点（我）' },
  ]
  const pad = 14; const boxW = 176; const boxH = items.length * 18 + 16
  const bx = pad; const by = SIZE - boxH - pad
  ctx.fillStyle = 'rgba(11,15,20,0.72)'; ctx.fillRect(bx, by, boxW, boxH)
  ctx.font = '11px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  items.forEach((it, i) => {
    const ly = by + 14 + i * 18, lx = bx + 14
    if (it.shape === 'dot') drawDot(ctx, lx / SIZE, ly / SIZE, 3.5, it.c)
    else if (it.shape === 'diamond') drawDiamond(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'x') drawX(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'ball') drawDot(ctx, lx / SIZE, ly / SIZE, 5, it.c, TEAM_COLOR.A)
    else if (it.shape === 'sq') { ctx.fillStyle = it.c; ctx.fillRect(lx - 3, ly - 3, 6, 6) }
    else if (it.shape === 'ring') { ctx.lineWidth = 2; ctx.strokeStyle = it.c; ctx.beginPath(); ctx.arc(lx / SIZE * SIZE, ly / SIZE * SIZE, 5, 0, Math.PI * 2); ctx.stroke() }
    ctx.fillStyle = '#e8edf4'; ctx.fillText(it.label, lx + 12, ly)
  })
}

function render() {
  const canvas = canvasRef.value; if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, SIZE, SIZE)
  if (radarImg.value) ctx.drawImage(radarImg.value, 0, 0, SIZE, SIZE)
  const shown = players.value.filter((p) => filter.value === 'all' || p.team_identity === filter.value)
  for (const player of shown) {
    const teamColor = TEAM_COLOR[player.team_identity] || '#9aa7b8'
    const isHi = highlight.value == null || highlight.value === player.player_id
    const isFocus = player.player_id === focusPlayerId.value
    drawPlayer(ctx, player, teamColor, isHi ? ROUTE_ALPHA : FADED_ALPHA, isFocus, !tickRateValid.value)
  }
  drawLegend(ctx)
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
function play() { if (reducedMotion || !maxDuration.value) return; if (currentTime.value >= maxDuration.value) currentTime.value = 0; playing.value = true; lastTs = 0; rafId = requestAnimationFrame(tick) }
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
    const data = await api.get(`/api/demos/${props.demoId}/team/route?round_no=${selectedRound.value}`)
    if (gen !== loadGeneration) return
    team.value = data; highlight.value = null; resetPlayback()
  } catch (e) {
    if (gen !== loadGeneration) return
    error.value = e instanceof ApiError ? e.message : '团队回放数据加载失败'
    team.value = null
  } finally {
    if (gen === loadGeneration) { loading.value = false; scheduleRender() }
  }
}
function selectRound(v) { selectedRound.value = Number(v); loadRound() }
function setFilter(f) { filter.value = f }

watch([radarImg, players, team, highlight, filter], () => scheduleRender())
watch(() => props.maxRound, () => { if (props.maxRound > 0) selectedRound.value = 1 })

onMounted(async () => { if (props.maxRound > 0) { try { await loadRadar() } catch { /* 忽略 */ } await loadRound() } })
onBeforeUnmount(() => { pause() })
</script>

<template>
  <div class="tm" :data-side="round.side">
    <div v-if="loading" class="tm-state">加载团队回放…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else-if="team">
      <!-- 控件：回合、筛选、倍速 -->
      <div class="tm-controls">
        <label class="tm-field"><span class="tm-lbl">回合</span>
          <select class="input tm-sel" :value="selectedRound" @change="selectRound($event.target.value)">
            <option v-for="n in Math.max(1, props.maxRound)" :key="n" :value="n">R{{ n }}</option>
          </select>
        </label>
        <div class="tm-seg" role="group" aria-label="队伍筛选">
          <button v-for="f in [['all', '全部'], ['A', '队伍A'], ['B', '队伍B']]" :key="f[0]" class="tm-seg-btn"
            :class="{ on: filter === f[0] }" @click="setFilter(f[0])">{{ f[1] }}</button>
        </div>
        <div class="tm-spacer"></div>
        <div class="tm-seg" role="group" aria-label="倍速">
          <button v-for="v in [0.5, 1, 2]" :key="v" class="tm-seg-btn sm" :class="{ on: speed === v }" @click="setSpeed(v)">{{ v }}x</button>
        </div>
      </div>

      <!-- 诚实摘要 -->
      <div class="tm-summary">
        <span class="tm-sum-item">本回合 <b class="num">{{ summary.a }}</b> 队A / <b class="num">{{ summary.b }}</b> 队B</span>
        <span class="tm-sum-item">存活 <b class="num">{{ summary.alive }}</b>人</span>
        <span class="tm-sum-item">击杀 <b class="num">{{ summary.kills }}</b></span>
        <span class="tm-sum-item">胜方 {{ winner === 'A' ? '队伍A' : winner === 'B' ? '队伍B' : '未记录' }}</span>
      </div>

      <!-- 玩家 chips（点击突出单玩家，再点恢复） -->
      <div class="tm-chips" v-if="players.length">
        <button class="tm-chip" :class="{ on: highlight == null }" @click="highlight = null">全部</button>
        <button v-for="p in players" :key="p.player_id" class="tm-chip" :class="{ on: highlight === p.player_id, dead: p.outcome === 'dead' }"
          @click="setHighlight(p.player_id)">{{ p.name }}</button>
      </div>

      <div class="tm-stage">
        <canvas ref="canvasRef" :width="SIZE" :height="SIZE" class="tm-canvas" data-ready="ready"></canvas>
        <!-- 当前区域（焦点玩家 > 突出玩家 > 全部一致；未知区域时诚实显示） -->
        <div v-if="currentPlace" class="tm-region">当前区域：{{ currentPlace }}</div>
        <div class="tm-timeline">
          <button class="tm-play" :disabled="reducedMotion || !tickRateValid" @click="togglePlay" :aria-label="playing ? '暂停' : '播放'">{{ reducedMotion ? '⏵' : (playing ? '⏸' : '⏵') }}</button>
          <input class="tm-scrub" type="range" min="0" :max="tickRateValid ? maxDuration : 0" step="0.01" :value="currentTime" @input="onScrub" :disabled="!tickRateValid" aria-label="时间轴" />
          <span class="tm-time num" v-if="tickRateValid">当前 {{ fmtTime(currentTime) }} / {{ fmtTime(maxDuration) }}</span>
          <span class="tm-time num" v-else>时长不可核算</span>
        </div>
        <p v-if="reducedMotion" class="tm-hint">已开启减弱动效：拖动时间轴查看，不自动播放。</p>
        <p v-if="!tickRateValid" class="tm-hint">后端未提供可证明的 tick_rate，本回合时长无法核算，仅展示静态路线。</p>
        <p class="tm-hint">高亮单玩家：点击玩家 chip；再点恢复全部。坐标与 tick 见折叠诊断。</p>

        <!-- 诊断信息（坐标/tick 只在这里出现） -->
        <details class="tm-diag" v-if="round.start_tick != null">
          <summary>技术诊断 / 导出</summary>
          <div class="tm-diag-body">
            <p class="tm-diag-line">地图 {{ map.name }} · 换算 scale={{ map.scale }} · pos=({{ map.pos_x }}, {{ map.pos_y }})</p>
            <p class="tm-diag-line">回合 R{{ round.number }} · tick {{ round.start_tick }}–{{ round.end_tick }} · tick_rate={{ round.tick_rate }} · 胜方 {{ winner === 'A' ? '队伍A' : winner === 'B' ? '队伍B' : '未记录' }}</p>
            <p v-if="!tickRateValid" class="tm-diag-line">⚠ 该回合并未提供可用于核算时长的 tick_rate，时间轴已禁用。</p>
          </div>
        </details>
      </div>
    </template>
    <div v-else-if="!loading && !error" class="tm-state">暂无可回放内容</div>
  </div>
</template>

<style scoped>
.tm { display: flex; flex-direction: column; gap: 12px; }
.tm-state { color: var(--text-muted); text-align: center; padding: 28px; font-size: 13px; }
.tm-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.tm-field { display: flex; align-items: center; gap: 8px; }
.tm-lbl { font-size: 13px; color: var(--text-secondary); }
.tm-sel { width: auto; min-width: 90px; }
.tm-spacer { flex: 1; }
.tm-seg { display: flex; gap: 4px; }
.tm-seg-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 5px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.tm-seg-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.tm-seg-btn.sm { padding: 5px 9px; }
.tm-summary { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 13px; color: var(--text-secondary); }
.tm-sum-item b { font-size: 16px; color: var(--text-primary); }
.tm-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.tm-chip { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 4px 11px; border-radius: 999px; font-size: 12.5px; cursor: pointer; font-weight: 600; }
.tm-chip.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.tm-chip.dead { opacity: 0.75; }
.tm-stage { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.tm-canvas { width: 100%; max-width: 720px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); aspect-ratio: 1; }
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
</style>
