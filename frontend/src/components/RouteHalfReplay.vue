<script setup>
/**
 * 6R5B：半场多回合动态路线总览。
 * - 必须选 CT 或 T（无 all）；同屏展示焦点玩家在该阵营的全部回合
 * - 相对时间统一从 00:00 对齐；多颗小球分别代表各回合，标注 R1/R2…
 * - 默认不泄露未来路径：从出生点逐步长出；kill/death X/绿终点只在播放到达时出现
 * - 死亡回合小球停在死亡 X；存活回合到结束出现绿终点
 * - 阵色区分（CT 蓝 / T 橙），路线带透明度；回合 chips 可点击突出单回合、淡化其余
 * - 播放/暂停、时间轴、0.5x/1x/2x、reduced-motion
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import { routeReplayState } from '../stores/routeReplay'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
  playerId: { type: [Number, String], default: null },
})

const SIZE = 720
const SIDE_COLOR = { CT: '#58a6ff', T: '#f0a020' }
const MARK = {
  spawn: '#3fb950', ball: '#ffffff', end: '#3fb950',
  killFill: '#f0a020', death: '#f85149', text: '#e8edf4',
}
const ROUTE_ALPHA = 0.72
const FADED_ALPHA = 0.16

const canvasRef = ref(null)
const radarImg = ref(null)
const half = ref(null)
const loading = ref(false)
const error = ref('')
const playing = ref(false)
const currentTime = ref(0)   // 秒（相对 00:00 对齐）
const highlight = ref(null)  // null=全部；其他=突出某一回合
let rafId = 0
let lastTs = 0
let loadGeneration = 0

const side = computed({ get: () => routeReplayState.halfSide, set: (v) => { routeReplayState.halfSide = v } })
const speed = computed({ get: () => routeReplayState.speed, set: (v) => { routeReplayState.speed = v } })

const reducedMotion = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
  : false

const rounds = computed(() => half.value?.rounds || [])
const summary = computed(() => half.value?.summary || {})
const maxDuration = computed(() => half.value?.max_duration || 0)
const mapMeta = computed(() => half.value?.map || {})
const someUnusable = computed(() => rounds.value.some((r) => !(Number(r.tick_rate) > 0)))

function fmtTime(sec) {
  const s = Math.max(0, sec); const m = Math.floor(s / 60); const r = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

function drawDot(ctx, rx, ry, r, fill, stroke) {
  ctx.beginPath(); ctx.arc(rx * SIZE, ry * SIZE, r, 0, Math.PI * 2)
  ctx.fillStyle = fill; ctx.fill()
  if (stroke) { ctx.lineWidth = 2; ctx.strokeStyle = stroke; ctx.stroke() }
}
function drawX(ctx, rx, ry, size, color) {
  ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineCap = 'round'
  const x = rx * SIZE; const y = ry * SIZE
  ctx.beginPath(); ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size)
  ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size); ctx.stroke()
}
function drawDiamond(ctx, rx, ry, size, color) {
  const x = rx * SIZE; const y = ry * SIZE
  ctx.beginPath(); ctx.moveTo(x, y - size); ctx.lineTo(x + size, y); ctx.lineTo(x, y + size); ctx.lineTo(x - size, y)
  ctx.closePath(); ctx.fillStyle = color; ctx.fill()
}
function drawPath(ctx, pts, color, width, alpha) {
  if (pts.length < 2) return
  ctx.save(); ctx.globalAlpha = alpha
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.lineJoin = 'round'
  ctx.beginPath(); ctx.moveTo(pts[0].rx * SIZE, pts[0].ry * SIZE)
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].rx * SIZE, pts[i].ry * SIZE)
  ctx.stroke(); ctx.restore()
}
function drawLabel(ctx, rx, ry, text, sideColor) {
  ctx.font = 'bold 12px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  const x = rx * SIZE; const y = ry * SIZE
  const w = ctx.measureText(text).width
  ctx.fillStyle = 'rgba(11,15,20,0.85)'; ctx.fillRect(x + 9, y - 9, w + 6, 18)
  ctx.fillStyle = sideColor; ctx.fillText(text, x + 12, y)
}
function drawLegend(ctx, sideColor) {
  const items = [
    { c: MARK.spawn, shape: 'dot', label: '出生' },
    { c: MARK.killFill, shape: 'diamond', label: '击杀' },
    { c: MARK.death, shape: 'x', label: '死亡' },
    { c: MARK.ball, shape: 'ball', label: '当前' },
    { c: MARK.end, shape: 'sq', label: '存活终局' },
  ]
  const pad = 14; const boxW = 176; const boxH = items.length * 18 + 16
  const bx = pad; const by = SIZE - boxH - pad
  ctx.fillStyle = 'rgba(11,15,20,0.72)'; ctx.fillRect(bx, by, boxW, boxH)
  ctx.font = '11px system-ui, sans-serif'; ctx.textBaseline = 'middle'
  items.forEach((it, i) => {
    const ly = by + 14 + i * 18; const lx = bx + 14
    if (it.shape === 'dot') drawDot(ctx, lx / SIZE, ly / SIZE, 3.5, it.c)
    else if (it.shape === 'diamond') drawDiamond(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'x') drawX(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'ball') drawDot(ctx, lx / SIZE, ly / SIZE, 5, it.c, sideColor)
    else if (it.shape === 'sq') { ctx.fillStyle = it.c; ctx.fillRect(lx - 3, ly - 3, 6, 6) }
    ctx.fillStyle = MARK.text; ctx.fillText(it.label, lx + 12, ly)
  })
}

function roundUsable(round) {
  const r = Number(round.tick_rate)
  return Number.isFinite(r) && r > 0
}
function roundDeathTick(round) {
  const de = round.events?.death
  if (de) return de.tick
  for (const p of (round.points || [])) if ((p.events || []).includes('death')) return p.tick
  return null
}

function ballAt(round, time) {
  const pts = round.points
  if (!pts.length || !roundUsable(round)) return { px: null, py: null }
  const tick = round.start_tick + time * Number(round.tick_rate)
  if (tick <= pts[0].tick) return { px: pts[0].rx, py: pts[0].ry }
  const last = pts[pts.length - 1]
  if (tick >= last.tick) return { px: last.rx, py: last.ry }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1]
    if (tick >= a.tick && tick <= b.tick) {
      const f = b.tick === a.tick ? 0 : (tick - a.tick) / (b.tick - a.tick)
      return { px: a.rx + (b.rx - a.rx) * f, py: a.ry + (b.ry - a.ry) * f }
    }
  }
  return { px: last.rx, py: last.ry }
}

function drawRound(ctx, round, sideColor, alpha) {
  const pts = round.points
  if (!pts.length) return
  const t = currentTime.value
  // 后端未提供可核算的 tick_rate：完整路线 + 全部标记，不伪造时间（无当前球）
  if (!roundUsable(round)) {
    drawPath(ctx, pts, sideColor, 2.4, alpha)
    ctx.save(); ctx.globalAlpha = alpha
    for (const p of pts) for (const e of (p.events || [])) {
      if (e === 'spawn') drawDot(ctx, p.rx, p.ry, 4, MARK.spawn)
      else if (e === 'kill') drawDiamond(ctx, p.rx, p.ry, 5, MARK.killFill)
      else if (e === 'death') drawX(ctx, p.rx, p.ry, 7, MARK.death)
    }
    if (round.outcome === 'alive' && pts.length) {
      const last = pts[pts.length - 1]
      ctx.fillStyle = MARK.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
    }
    ctx.restore()
    return
  }
  const cTick = round.start_tick + t * Number(round.tick_rate)
  const ball = ballAt(round, t)

  // 已探索路径（到当前小球）
  if (ball.px !== null) {
    const explored = pts.filter((p) => p.tick <= cTick)
    const pathPts = explored.length ? explored : [pts[0]]
    drawPath(ctx, pathPts, sideColor, 2.4, alpha)
    if (explored.length && explored[explored.length - 1].tick < cTick) {
      const lastPt = explored[explored.length - 1]
      ctx.save(); ctx.globalAlpha = alpha
      ctx.strokeStyle = sideColor; ctx.lineWidth = 2.4
      ctx.beginPath()
      ctx.moveTo(lastPt.rx * SIZE, lastPt.ry * SIZE)
      ctx.lineTo(ball.px * SIZE, ball.py * SIZE)
      ctx.stroke(); ctx.restore()
    }
  }

  // 标记/球/标签：整体遵循 alpha（非目标回合随突出降透明度）
  ctx.save(); ctx.globalAlpha = alpha
  for (const p of pts) {
    for (const e of (p.events || [])) {
      if (e === 'spawn') drawDot(ctx, p.rx, p.ry, 4, MARK.spawn)
      else if (e === 'kill' && p.tick <= cTick) drawDiamond(ctx, p.rx, p.ry, 5, MARK.killFill)
      else if (e === 'death' && p.tick <= cTick) drawX(ctx, p.rx, p.ry, 7, MARK.death)
    }
  }
  // 存活回合到结束 → 绿终点
  if (round.outcome === 'alive' && round.end_tick <= cTick && pts.length) {
    const last = pts[pts.length - 1]
    ctx.fillStyle = MARK.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
  }
  // 死亡回合：到达死亡 tick 后移除「当前」球，只留唯一 X（不叠加）。
  const deadTick = roundDeathTick(round)
  const deadNow = round.outcome === 'dead' && deadTick != null && cTick >= deadTick
  // 小球 + R 标签
  if (ball.px !== null && !deadNow) {
    drawDot(ctx, ball.px, ball.py, 6, MARK.ball, sideColor)
    drawLabel(ctx, ball.px, ball.py, 'R' + round.round, sideColor)
  }
  ctx.restore()
}

function render() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, SIZE, SIZE)
  if (radarImg.value) ctx.drawImage(radarImg.value, 0, 0, SIZE, SIZE)
  const sideColor = SIDE_COLOR[side.value] || SIDE_COLOR.CT
  if (rounds.value.length) {
    for (const round of rounds.value) {
      const isHi = highlight.value == null || highlight.value === round.round
      drawRound(ctx, round, sideColor, isHi ? ROUTE_ALPHA : FADED_ALPHA)
    }
  }
  drawLegend(ctx, sideColor)
}

let renderPending = false
function scheduleRender(retries = 0) {
  if (renderPending) return
  renderPending = true
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
function play() {
  if (reducedMotion || !maxDuration.value) return
  if (currentTime.value >= maxDuration.value) currentTime.value = 0
  playing.value = true; lastTs = 0; rafId = requestAnimationFrame(tick)
}
function pause() { playing.value = false; if (rafId) cancelAnimationFrame(rafId) }
function togglePlay() { playing.value ? pause() : play() }
function setSpeed(v) { speed.value = v }
function onScrub(e) { pause(); currentTime.value = Math.min(Number(e.target.value), maxDuration.value); render() }
function setHighlight(r) { highlight.value = highlight.value === r ? null : r }
function resetPlayback() { pause(); currentTime.value = 0; render() }

async function loadRadar() {
  const img = new Image(); img.crossOrigin = 'anonymous'
  img.src = `/api/maps/${props.mapName}/radar`
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej })
  radarImg.value = img; scheduleRender()
}

async function loadHalf() {
  const gen = ++loadGeneration
  loading.value = true; error.value = ''
  try {
    const data = await api.get(`/api/demos/${props.demoId}/personal/route?view=half&side=${side.value}`)
    if (gen !== loadGeneration) return
    half.value = data
    highlight.value = null
    resetPlayback()
  } catch (e) {
    if (gen !== loadGeneration) return
    error.value = e instanceof ApiError ? e.message : '半场总览数据加载失败'
    half.value = null
  } finally {
    if (gen === loadGeneration) { loading.value = false; scheduleRender() }
  }
}

function changeSide(s) { side.value = s; loadHalf() }

watch([radarImg, rounds, half, highlight], () => scheduleRender())
watch(() => props.playerId, () => { if (props.playerId != null) { resetPlayback(); loadHalf() } })

onMounted(async () => { if (props.playerId != null) { try { await loadRadar() } catch { /* 忽略 */ } await loadHalf() } })
onBeforeUnmount(() => { pause() })
</script>

<template>
  <div class="hr" :data-side="side">
    <div v-if="loading" class="hr-state">加载半场总览…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else>
      <!-- 紧凑摘要 -->
      <div class="hr-summary" v-if="summary.rounds != null">
        <span class="hr-sum-item"><b class="num">{{ summary.rounds }}</b> 回合</span>
        <span class="hr-sum-item">{{ summary.dead }} 死</span>
        <span class="hr-sum-item">{{ summary.alive }} 存活</span>
        <span class="hr-sum-item">存活率 <b class="num">{{ Math.round((summary.survival_rate || 0) * 100) }}%</b></span>
        <span class="hr-sum-item">{{ summary.kills }} 击杀</span>
      </div>

      <!-- 阵营（必须 CT 或 T） -->
      <div class="hr-sides" role="group" aria-label="阵营">
        <button v-for="s in ['CT', 'T']" :key="s" class="hr-side" :class="{ on: side === s }" @click="changeSide(s)">{{ s }}</button>
      </div>

      <!-- 回合 chips（点击突出单回合，再点恢复全部） -->
      <div class="hr-chips" v-if="rounds.length">
        <button class="hr-chip" :class="{ on: highlight == null }" @click="highlight = null">全部</button>
        <button v-for="r in rounds" :key="r.round" class="hr-chip" :class="{ on: highlight === r.round, dead: r.outcome === 'dead' }"
          @click="setHighlight(r.round)">R{{ r.round }}</button>
      </div>

      <div v-if="!rounds.length && !error" class="hr-state">该阵营没有可回放的回合</div>

      <div v-else class="hr-stage">
        <canvas ref="canvasRef" :width="SIZE" :height="SIZE" class="hr-canvas" data-ready="ready"></canvas>

        <div v-if="half" class="hr-timeline">
          <button class="hr-play" :disabled="reducedMotion" @click="togglePlay" :aria-label="playing ? '暂停' : '播放'">
            {{ reducedMotion ? '⏵' : (playing ? '⏸' : '⏵') }}</button>
          <input class="hr-scrub" type="range" min="0" :max="maxDuration" step="0.01" :value="currentTime"
            @input="onScrub" :disabled="!maxDuration" aria-label="时间轴" />
          <span class="hr-time num">当前 {{ fmtTime(currentTime) }} / {{ fmtTime(maxDuration) }}</span>
        </div>

        <p v-if="reducedMotion" class="hr-hint">已开启减弱动效：拖动时间轴查看，不自动播放。</p>
        <p v-if="someUnusable" class="hr-hint">部分回合缺少可证明的 tick_rate，时长无法核算，仅展示其静态路线。</p>
        <p class="hr-hint">高亮单回合：点击 R 芯片；再点恢复全部。</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.hr { display: flex; flex-direction: column; gap: 12px; }
.hr-state { color: var(--text-muted); text-align: center; padding: 28px; font-size: 13px; }

.hr-summary { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 13px; color: var(--text-secondary); }
.hr-sum-item b { font-size: 16px; color: var(--text-primary); }

.hr-sides { display: flex; gap: 6px; }
.hr-side { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary);
  padding: 5px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.hr-side.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }

.hr-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.hr-chip { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary);
  padding: 4px 11px; border-radius: 999px; font-size: 12.5px; cursor: pointer; font-weight: 600; }
.hr-chip.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.hr-chip.dead { opacity: 0.75; }

.hr-stage { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.hr-canvas { width: 100%; max-width: 720px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); aspect-ratio: 1; }

.hr-timeline { display: flex; align-items: center; gap: 12px; width: 100%; max-width: 720px; }
.hr-play { width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--border-strong);
  background: var(--accent); color: #14100a; font-size: 18px; cursor: pointer; flex: 0 0 auto; }
.hr-play:disabled { opacity: 0.5; cursor: not-allowed; }
.hr-scrub { flex: 1; accent-color: var(--accent); }
.hr-time { font-size: 12.5px; color: var(--text-secondary); min-width: 118px; text-align: right; }
.hr-hint { font-size: 12px; color: var(--text-muted); }
</style>
