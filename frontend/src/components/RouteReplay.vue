<script setup>
/**
 * 6R4：个人动态路线回放（主视觉 RouteReplay）。
 * - Canvas 雷达底图（/api/maps/{map}/radar）+ rx/ry 归一化坐标（服务端按 maps.json 换算）
 * - spawn → 时间推进路线 → 移动小球；死亡画 X、存活显示终局状态
 * - 控件：回合选择、CT/T 分阵营、播放/暂停、时间拖动条、0.5x/1x/2x、当前回合时间、图例
 * - 状态：加载 / 空态 / 部分数据(警告) / 错误；reduced-motion 不自动播放
 * - 坐标仅出现在诊断信息（可折叠），不作为主界面
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import RouteHalfReplay from './RouteHalfReplay.vue'
import { routeReplayState } from '../stores/routeReplay'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
  playerId: { type: [Number, String], default: null },
  playerName: { type: String, default: '' },
})

const SIZE = 720
const COLORS = {
  route: '#f0a020',          // 路线/探索路径（电竞橙）
  routeFaint: 'rgba(240,160,32,0.16)',
  spawn: '#3fb950',          // 出生点（绿）
  kill: '#f0a020',           // 击杀点（橙菱形）
  death: '#f85149',          // 死亡 X（红）
  ball: '#ffffff',           // 当前小球
  end: '#3fb950',            // 存活终局（绿方块）
  text: '#9aa7b8',
  panel: 'rgba(11,15,20,0.72)',
}

const canvasRef = ref(null)
const radarImg = ref(null)
const rounds = ref([])
const route = ref(null)
const loading = ref(false)
const error = ref('')
const playing = ref(false)
const currentTime = ref(0)   // 秒（相对回合开始）
const showDiag = ref(false)
let rafId = 0
let lastTs = 0
let loadGeneration = 0

const side = computed({
  get: () => routeReplayState.side,
  set: (v) => { routeReplayState.side = v },
})
const speed = computed({
  get: () => routeReplayState.speed,
  set: (v) => { routeReplayState.speed = v },
})
const selectedRound = computed(() => routeReplayState.round)

const reducedMotion = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
  : false

// 时间原点：00:00 = play_start_tick（真实 freeze_end/round_live 边界），绝不固定减 10 秒
const playStartTick = computed(() => route.value?.round?.play_start_tick ?? route.value?.round?.freeze_end_tick ?? route.value?.round?.start_tick ?? 0)
// 只信任后端提供的 tick_rate：为正数才核算秒数；缺失/为 0/不可证明时绝不伪造秒数，
// 改为「时长不可核算」状态（不做时间推进、不渲染误导性的 mm:ss）。
const tickRate = computed(() => {
  const r = Number(route.value?.round?.tick_rate)
  return Number.isFinite(r) && r > 0 ? r : null
})
const tickRateValid = computed(() => tickRate.value != null)
const roundDuration = computed(() => {
  if (!route.value?.round) return 0
  const d = route.value.round.end_tick - playStartTick.value
  if (!tickRateValid.value) return null   // 后端未证明 tick_rate：无法核算时长
  return Math.max(0, d) / tickRate.value
})
const startTick = computed(() => playStartTick.value)
const points = computed(() => route.value?.points || [])
const integrity = computed(() => route.value?.integrity || {})
const outcome = computed(() => route.value?.outcome || 'unknown')
const mapMeta = computed(() => route.value?.map || {})

// 结局死亡时间：来自 events.death.tick 与 round.start_tick/tick_rate（与 currentTime 无关）
const deathTime = computed(() => {
  const d = route.value?.events?.death
  const r = route.value?.round
  if (!d || !r) return null
  if (!tickRateValid.value) return null
  return (d.tick - playStartTick.value) / tickRate.value
})
const showFullRoute = computed({
  get: () => routeReplayState.showFullRoute,
  set: (v) => { routeReplayState.showFullRoute = v },
})
const mode = computed({
  get: () => routeReplayState.mode,
  set: (v) => { routeReplayState.mode = v },
})
function setMode(v) { mode.value = v }
const currentTick = computed(() => (tickRateValid.value ? startTick.value + currentTime.value * tickRate.value : 0))

// 当前区域：取时间上最接近播放时刻的采样点的 place（已是中文叫位或“未知区域”）
const currentPlace = computed(() => {
  if (!tickRateValid.value) return '未知区域'
  const pts = points.value
  if (!pts.length) return '未知区域'
  const tick = startTick.value + currentTime.value * tickRate.value
  let best = pts[0]
  let bestD = Infinity
  for (const p of pts) {
    const d = Math.abs((p.tick ?? 0) - tick)
    if (d < bestD) { bestD = d; best = p }
  }
  return best.place || '未知区域'
})

// 当前小球位置（在 points 之间插值）
function ballAt(time) {
  const pts = points.value
  if (!pts.length) return { px: null, py: null, idx: -1 }
  const tick = startTick.value + time * tickRate.value
  if (tick <= pts[0].tick) return { px: pts[0].rx, py: pts[0].ry, idx: 0 }
  const last = pts[pts.length - 1]
  if (tick >= last.tick) return { px: last.rx, py: last.ry, idx: pts.length - 1 }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i]; const b = pts[i + 1]
    if (tick >= a.tick && tick <= b.tick) {
      const f = b.tick === a.tick ? 0 : (tick - a.tick) / (b.tick - a.tick)
      return {
        px: a.rx + (b.rx - a.rx) * f,
        py: a.ry + (b.ry - a.ry) * f,
        idx: i,
        exact: false,
      }
    }
  }
  return { px: last.rx, py: last.ry, idx: pts.length - 1 }
}

function fmtTime(sec) {
  const s = Math.max(0, sec)
  const m = Math.floor(s / 60)
  const r = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

function drawDot(ctx, rx, ry, r, fill, stroke) {
  ctx.beginPath()
  ctx.arc(rx * SIZE, ry * SIZE, r, 0, Math.PI * 2)
  ctx.fillStyle = fill
  ctx.fill()
  if (stroke) { ctx.lineWidth = 2; ctx.strokeStyle = stroke; ctx.stroke() }
}

function drawX(ctx, rx, ry, size, color) {
  ctx.strokeStyle = color
  ctx.lineWidth = 3
  ctx.lineCap = 'round'
  const x = rx * SIZE; const y = ry * SIZE
  ctx.beginPath()
  ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size)
  ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size)
  ctx.stroke()
}

function drawDiamond(ctx, rx, ry, size, color) {
  const x = rx * SIZE; const y = ry * SIZE
  ctx.beginPath()
  ctx.moveTo(x, y - size); ctx.lineTo(x + size, y); ctx.lineTo(x, y + size); ctx.lineTo(x - size, y)
  ctx.closePath()
  ctx.fillStyle = color
  ctx.fill()
}

function drawPath(ctx, pts, color, width, dash) {
  if (pts.length < 2) return
  ctx.strokeStyle = color
  ctx.lineWidth = width
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.setLineDash(dash || [])
  ctx.beginPath()
  ctx.moveTo(pts[0].rx * SIZE, pts[0].ry * SIZE)
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].rx * SIZE, pts[i].ry * SIZE)
  ctx.stroke()
  ctx.setLineDash([])
}

function drawLegend(ctx) {
  const pad = 14
  const items = [
    { c: COLORS.spawn, shape: 'dot', label: '出生' },
    { c: COLORS.kill, shape: 'diamond', label: '击杀' },
    { c: COLORS.death, shape: 'x', label: '死亡' },
    { c: COLORS.ball, shape: 'ball', label: '当前' },
    { c: COLORS.end, shape: 'sq', label: '存活终局' },
  ]
  ctx.fillStyle = COLORS.panel
  const boxW = 176; const boxH = items.length * 18 + 16
  const bx = pad; const by = SIZE - boxH - pad
  ctx.fillRect(bx, by, boxW, boxH)
  ctx.font = '11px system-ui, sans-serif'
  ctx.textBaseline = 'middle'
  items.forEach((it, i) => {
    const ly = by + 14 + i * 18
    const lx = bx + 14
    if (it.shape === 'dot') drawDot(ctx, lx / SIZE, ly / SIZE, 3.5, it.c)
    else if (it.shape === 'diamond') drawDiamond(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'x') drawX(ctx, lx / SIZE, ly / SIZE, 4, it.c)
    else if (it.shape === 'ball') { drawDot(ctx, lx / SIZE, ly / SIZE, 5, it.c, COLORS.route) }
    else if (it.shape === 'sq') { ctx.fillStyle = it.c; ctx.fillRect(lx - 3, ly - 3, 6, 6) }
    ctx.fillStyle = COLORS.text
    ctx.fillText(it.label, lx + 12, ly)
  })
}

function drawMarkers(ctx, p, limitTick) {
  // 出生点始终可见；kill/death 标记仅在"播放时间到达事件"时出现（limitTick=当前tick；完整路线=Infinity）
  const evs = Array.isArray(p.events) && p.events.length ? p.events : (p.event ? [p.event] : [])
  for (const e of evs) {
    if (e === 'spawn') drawDot(ctx, p.rx, p.ry, 4.5, COLORS.spawn)
    else if (e === 'kill' && p.tick <= limitTick) drawDiamond(ctx, p.rx, p.ry, 5, COLORS.kill)
    else if (e === 'death' && p.tick <= limitTick) drawX(ctx, p.rx, p.ry, 7, COLORS.death)
  }
}

function render() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, SIZE, SIZE)
  if (radarImg.value) ctx.drawImage(radarImg.value, 0, 0, SIZE, SIZE)

  const pts = points.value
  if (!pts.length) { drawLegend(ctx); return }

  // 后端未提供可证明的 tick_rate：无法核算秒数 → 完整静态路线 + 全部事件标记（不伪造时间/进度）
  if (!tickRateValid.value) {
    drawPath(ctx, pts, COLORS.routeFaint, 2)
    for (const p of pts) drawMarkers(ctx, p, Number.POSITIVE_INFINITY)
    if (outcome.value === 'alive' && pts.length) {
      const last = pts[pts.length - 1]
      ctx.fillStyle = COLORS.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
    }
    drawLegend(ctx)
    return
  }

  const cTick = currentTick.value
  const ball = ballAt(currentTime.value)

  if (showFullRoute.value) {
    // 显式「显示完整路线」：整场淡路线 + 全部标记（预览，不泄露默认视图）
    drawPath(ctx, pts, COLORS.routeFaint, 2)
    const gapTicks = 2 * tickRate.value
    for (let i = 0; i < pts.length - 1; i++) {
      if (pts[i + 1].tick - pts[i].tick > gapTicks) {
        drawPath(ctx, [pts[i], pts[i + 1]], 'rgba(240,160,32,0.35)', 1.5, [4, 4])
      }
    }
    for (const p of pts) drawMarkers(ctx, p, Number.POSITIVE_INFINITY)
    if (outcome.value === 'alive' && pts.length) {
      const last = pts[pts.length - 1]
      ctx.fillStyle = COLORS.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8)
    }
  } else {
    // 默认：只显示已探索路径（到当前小球）；标记仅在到达时出现
    if (ball.idx >= 0) {
      const explored = pts.slice(0, ball.idx + 1)
      drawPath(ctx, explored, COLORS.route, 2.5)
      if (!ball.exact && ball.px !== null) {
        const lastPt = explored[explored.length - 1]
        ctx.strokeStyle = COLORS.route; ctx.lineWidth = 2.5
        ctx.beginPath()
        ctx.moveTo(lastPt.rx * SIZE, lastPt.ry * SIZE)
        ctx.lineTo(ball.px * SIZE, ball.py * SIZE)
        ctx.stroke()
      }
    }
    for (const p of pts) drawMarkers(ctx, p, cTick)
    if (outcome.value === 'alive' && pts.length) {
      const last = pts[pts.length - 1]
      if (last.tick <= cTick) { ctx.fillStyle = COLORS.end; ctx.fillRect(last.rx * SIZE - 4, last.ry * SIZE - 4, 8, 8) }
    }
  }

  // 小球（当前播放位置）。死亡到达后移除“当前”标识，只留唯一 X（不叠加）。
  const deathTick = route.value?.events?.death?.tick
  const currentGone = outcome.value === 'dead' && deathTick != null && cTick >= deathTick
  if (ball.px !== null && !currentGone) drawDot(ctx, ball.px, ball.py, 6, COLORS.ball, COLORS.route)

  drawLegend(ctx)
}

// Vue nextTick 排程渲染：保证 Canvas 挂载后立即绘制；底图/路线任意先后到达最终都会重绘。
// 不用 setTimeout 猜时序，而是用 nextTick + 有界重试（绑定 Vue 的 DOM flush 周期）。
let renderPending = false
function scheduleRender(retries = 0) {
  if (renderPending) return
  renderPending = true
  nextTick(() => {
    renderPending = false
    if (canvasRef.value) { render(); return }
    // Canvas 尚未挂载（loading/空态/异步 DOM flush）→ 下一个 nextTick 重试（有界）
    if (retries < 40) scheduleRender(retries + 1)
  })
}

watch([radarImg, points, showFullRoute], () => scheduleRender())

function tick(ts) {
  if (!playing.value) return
  if (lastTs) {
    const dt = (ts - lastTs) / 1000
    currentTime.value = Math.min(currentTime.value + dt * speed.value, roundDuration.value)
    if (currentTime.value >= roundDuration.value) {
      currentTime.value = roundDuration.value
      playing.value = false
    }
    render()
  }
  lastTs = ts
  if (playing.value) rafId = requestAnimationFrame(tick)
}

function play() {
  if (reducedMotion || !roundDuration.value) return
  if (currentTime.value >= roundDuration.value) currentTime.value = 0
  playing.value = true
  lastTs = 0
  rafId = requestAnimationFrame(tick)
}
function pause() {
  playing.value = false
  if (rafId) cancelAnimationFrame(rafId)
}
function togglePlay() { playing.value ? pause() : play() }
function setSpeed(v) { speed.value = v }
function onScrub(e) {
  pause()
  currentTime.value = Number(e.target.value)
  scheduleRender()
}

function resetPlayback() {
  pause()
  currentTime.value = 0
  scheduleRender()
}

async function loadRoute(roundNo) {
  const gen = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/api/demos/${props.demoId}/personal/route?round_no=${roundNo}&side=${side.value}`)
    if (gen !== loadGeneration) return
    route.value = data
    resetPlayback()
  } catch (e) {
    if (gen !== loadGeneration) return
    error.value = e instanceof ApiError ? e.message : '路线数据加载失败'
    route.value = null
  } finally {
    if (gen === loadGeneration) { loading.value = false; scheduleRender() }
  }
}

async function loadRounds() {
  const gen = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/api/demos/${props.demoId}/personal/route?side=${side.value}`)
    if (gen !== loadGeneration) return
    rounds.value = data.rounds || []
    // 选中回合：优先保留 store.round；否则取首个
    let r = routeReplayState.round
    if (!rounds.value.find((x) => x.round === r)) r = rounds.value[0]?.round || null
    routeReplayState.round = r
    if (r != null) await loadRoute(r)
    else { route.value = null; resetPlayback() }
  } catch (e) {
    if (gen !== loadGeneration) return
    error.value = e instanceof ApiError ? e.message : '路线数据加载失败'
    rounds.value = []
    route.value = null
  } finally {
    if (gen === loadGeneration) { loading.value = false; scheduleRender() }
  }
}

function selectRound(r) {
  routeReplayState.round = Number(r)
  loadRoute(routeReplayState.round)
}
function changeSide(s) {
  side.value = s
  loadRounds()
}

async function loadRadar() {
  const img = new Image()
  img.crossOrigin = 'anonymous'
  img.src = `/api/maps/${props.mapName}/radar`
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej })
  radarImg.value = img
  scheduleRender()
}

const canProceed = computed(() => props.playerId != null)

async function init() {
  if (!canProceed.value) return
  try { await loadRadar() } catch { /* 底图失败仍显示错误/空态 */ }
  await loadRounds()
}

watch(() => props.playerId, () => { if (props.playerId != null) { resetPlayback(); loadRounds() } })

onMounted(init)
onBeforeUnmount(() => { pause() })
</script>

<template>
  <div class="rr" :data-outcome="outcome">
    <!-- 模式切换：单回合回放 / 半场总览 -->
    <div class="rr-mode">
      <button class="rr-mode-btn" :class="{ on: mode === 'single' }" @click="setMode('single')">单回合回放</button>
      <button class="rr-mode-btn" :class="{ on: mode === 'half' }" @click="setMode('half')">半场总览</button>
    </div>

    <template v-if="mode === 'single'">
      <div v-if="loading" class="rr-state">加载路线…</div>
      <p v-else-if="error" class="form-error-box">{{ error }}</p>

      <template v-else>
      <!-- 控件条 -->
      <div class="rr-controls">
        <label class="rr-field">
          <span class="rr-lbl">回合</span>
          <select class="input rr-sel" :value="route?.round?.number ?? ''" @change="selectRound($event.target.value)">
            <option value="" disabled>选择回合</option>
            <option v-for="r in rounds" :key="r.round" :value="r.round">
              R{{ r.round }} · {{ r.side || '—' }}{{ r.outcome === 'dead' ? ' · 死亡' : '' }}
            </option>
          </select>
        </label>
        <div class="rr-seg" role="group" aria-label="阵营筛选">
          <button v-for="s in ['all', 'CT', 'T']" :key="s" class="rr-seg-btn" :class="{ on: side === s }"
            @click="changeSide(s)">{{ s === 'all' ? '全部' : s }}</button>
        </div>
        <div class="rr-spacer"></div>
        <label class="rr-toggle">
          <input type="checkbox" v-model="showFullRoute" />
          <span>显示完整路线</span>
        </label>
        <div class="rr-seg" role="group" aria-label="倍速">
          <button v-for="v in [0.5, 1, 2]" :key="v" class="rr-seg-btn sm" :class="{ on: speed === v }"
            @click="setSpeed(v)">{{ v }}x</button>
        </div>
      </div>

      <!-- 状态：无回合 -->
      <div v-if="!rounds.length && !error" class="rr-state">该筛选下没有可回放的回合</div>

      <!-- 路线画布 -->
      <div v-else class="rr-stage">
        <canvas ref="canvasRef" :width="SIZE" :height="SIZE" class="rr-canvas" data-ready="ready"></canvas>

        <!-- 播放控制 -->
        <div v-if="route" class="rr-timeline">
          <button class="rr-play" :disabled="reducedMotion || !tickRateValid" @click="togglePlay" :aria-label="playing ? '暂停' : '播放'">
            {{ reducedMotion ? '⏵' : (playing ? '⏸' : '⏵') }}
          </button>
          <input class="rr-scrub" type="range" min="0" :max="roundDuration || 0" step="0.01"
            :value="currentTime" @input="onScrub" :disabled="!tickRateValid" aria-label="时间轴" />
          <span class="rr-time num" v-if="tickRateValid">当前 {{ fmtTime(currentTime) }} / {{ fmtTime(roundDuration) }}</span>
          <span class="rr-time num" v-else>时长不可核算</span>
        </div>

        <!-- 当前区域（中文叫位；未知区域时诚实显示） -->
        <div v-if="route" class="rr-region">当前区域：{{ currentPlace }}</div>

        <p v-if="reducedMotion" class="rr-hint">已开启减弱动效：拖动时间轴查看路线，不自动播放。</p>
        <p v-if="route && !tickRateValid" class="rr-hint">后端未提供可证明的 tick_rate，本回合时长无法核算，仅展示静态路线。</p>
        <p v-if="outcome === 'dead' && deathTime != null" class="rr-outcome dead">本回合结果：死亡 · 死亡于 {{ fmtTime(deathTime) }}</p>
        <p v-else-if="outcome === 'alive' && tickRateValid" class="rr-outcome alive">本回合结果：存活（{{ fmtTime(roundDuration) }} 全场结束）</p>

        <!-- 完整性/部分数据 -->
        <div v-if="integrity.warnings?.length" class="rr-warn">
          <span v-for="(w, i) in integrity.warnings" :key="i" class="rr-warn-item">⚠ {{ w }}</span>
        </div>

        <!-- 诊断信息（坐标只在这里出现） -->
        <details class="rr-diag" v-if="mapMeta.name">
          <summary @click="showDiag = !showDiag">技术诊断 / 导出</summary>
          <div v-if="showDiag" class="rr-diag-body">
            <p class="rr-diag-line">
              地图 {{ mapMeta.name }} · 换算 scale={{ mapMeta.scale }} · pos=({{ mapMeta.pos_x }}, {{ mapMeta.pos_y }})
            </p>
            <p class="rr-diag-line">玩家 {{ playerName || route?.player?.name }} · 本回合采样 {{ integrity.sampled_points }} 点 / 保留 {{ integrity.kept_points }} 点（丢弃 {{ integrity.dropped_points }}）· 覆盖率 {{ Math.round((integrity.coverage || 0) * 100) }}%</p>
            <p v-if="route?.events?.spawn" class="rr-diag-line">出生点世界坐标 ({{ route.events.spawn.x }}, {{ route.events.spawn.y }})</p>
            <p v-if="route?.events?.death" class="rr-diag-line">死亡点世界坐标 ({{ route.events.death.x }}, {{ route.events.death.y }})</p>
            <p class="rr-diag-line">轨迹 tick 有序：{{ integrity.ordered ? '是' : '否' }}</p>
          </div>
        </details>
      </div>
      </template>
    </template>

    <!-- 半场总览 -->
    <RouteHalfReplay v-else :demo-id="demoId" :map-name="mapName" :player-id="playerId" :player-name="playerName" />
  </div>
</template>

<style scoped>
.rr { display: flex; flex-direction: column; gap: 12px; }
.rr-mode { display: flex; gap: 6px; }
.rr-mode-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary);
  padding: 5px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.rr-mode-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.rr-state { color: var(--text-muted); text-align: center; padding: 32px; font-size: 13px; }

.rr-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.rr-field { display: flex; align-items: center; gap: 8px; }
.rr-lbl { font-size: 13px; color: var(--text-secondary); }
.rr-sel { width: auto; min-width: 140px; }
.rr-spacer { flex: 1; }
.rr-seg { display: flex; gap: 4px; }
.rr-seg-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary);
  padding: 5px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.rr-seg-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.rr-seg-btn.sm { padding: 5px 9px; }
.rr-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text-secondary); cursor: pointer; white-space: nowrap; }
.rr-toggle input { accent-color: var(--accent); }

.rr-stage { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.rr-canvas { width: 100%; max-width: 720px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); aspect-ratio: 1; }

.rr-timeline { display: flex; align-items: center; gap: 12px; width: 100%; max-width: 720px; }
.rr-play { width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--border-strong);
  background: var(--accent); color: #14100a; font-size: 18px; cursor: pointer; flex: 0 0 auto; }
.rr-play:disabled { opacity: 0.5; cursor: not-allowed; }
.rr-scrub { flex: 1; accent-color: var(--accent); }
.rr-time { font-size: 12.5px; color: var(--text-secondary); min-width: 118px; text-align: right; }

.rr-hint { font-size: 12px; color: var(--text-muted); }
.rr-region { width: 100%; max-width: 720px; font-size: 12.5px; color: var(--text-secondary);
  padding: 5px 10px; border-radius: 6px; background: var(--bg-elevated); border: 1px solid var(--border); }
.rr-outcome { font-size: 13px; font-weight: 700; }
.rr-outcome.dead { color: var(--err); }
.rr-outcome.alive { color: var(--ok); }

.rr-warn { display: flex; flex-direction: column; gap: 4px; width: 100%; max-width: 720px; }
.rr-warn-item { font-size: 12px; color: var(--warn); }

.rr-diag { width: 100%; max-width: 720px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-elevated); padding: 8px 12px; }
.rr-diag summary { cursor: pointer; font-size: 12.5px; color: var(--text-secondary); }
.rr-diag-body { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.rr-diag-line { font-size: 12px; color: var(--text-muted); font-family: ui-monospace, monospace; }

@media (max-width: 720px) {
  .rr-controls { gap: 8px; }
  .rr-field { flex: 1 1 100%; }
  .rr-sel { min-width: 0; flex: 1; }
}
</style>
