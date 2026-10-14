<script setup>
// 6R21F-E6A 可复用产品回放组件：真实 demo19 × Mirage NavMesh 连续控制场（单一真值引擎 = ../map-control/mapControlEngine.js）。
// 产品风格 Canvas 渲染：控制场端点纹理交叉淡入淡出 + 前线交叉淡入；玩家/击杀/补枪/闪/死亡X 同时间轴；
// 默认「雾(标量场)」关、「控制前线」开，图层独立开关；共享 map transform/timeline/clip-mask（可玩区）。
// 不与实验室复制真值：本组件 = 引擎 + 渲染适配层；实验室仅诊断壳。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { createMapControlEngine } from '../map-control/mapControlEngine.js'
import { replayConfigFor } from '../map-control/mapCatalog.js'
import { mapLabel } from '../utils/mapNames'

const props = defineProps({
  sample: { type: Object, required: true },
  nav: { type: Object, required: true },
})

const CANVAS = 720
const GRID_N = 128
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200 }

const canvasRef = ref(null)
const playing = ref(false)
const currentTime = ref(0)
const speed = ref(1)
const reduced = ref(false)
let engine = null
let compositeCanvas = null
let radarImg = null
let radarPixels = null // 预渲染 radar 到 720x720 ImageData；之后 putImageData（CPU 确定性，且避免每帧 CPU scaled drawImage 重采样开销）
let rafId = 0, lastFrame = 0
let ready = false

// 图层开关（默认：雾 off、前线 on）
const layers = reactive({
  scalar: false, front: true, players: true, events: true, kills: true, flash: true, deathX: true,
})
// 事件开关注册为受控；fog 为原"雾"语义，此处开放为标量场层。

const duration = computed(() => props.sample?.round?.max_duration || 0)
const clock = computed(() => { const s = Math.max(0, Math.min(duration.value, currentTime.value)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}` })
// E6D：地图名 / 真实 areaCount 一律由样本与 NavMesh 推导（不硬编码 Mirage）。
const mapName = computed(() => props.sample?.map?.name || '')
const areaCount = computed(() => (props.nav?.areas?.length) || (props.nav?.stats?.areaCount) || 0)

onMounted(() => {
  reduced.value = typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  radarImg = new Image()
  // E6D：radar 底图按权威 mapName 经目录选择后端 /api/maps/{map}/radar（de_inferno 无静态 /maps/*.png）。
  const rc = replayConfigFor(mapName.value)
  radarImg.src = rc?.radarUrl || `/maps/${mapName.value}.png`
  radarImg.onload = () => { prepRadar(); scheduleRender() }
  engine = createMapControlEngine({ nav: props.nav, sample: props.sample, cfg: CFG, opts: { gridN: GRID_N }, hooks: { ready: () => { warm(); scheduleRender() }, frame: () => scheduleRender() } })
  compositeCanvas = document.createElement('canvas'); compositeCanvas.width = GRID_N; compositeCanvas.height = GRID_N
  engine.initWorker()
  currentTime.value = initialTime()
  warm()
  scheduleRender()
})
// 预渲染 radar 一次到 720x720 ImageData：每帧用 putImageData（确定性 + 无 CPU 缩放重采样）。只执行一次，缓存固定结果。
function prepRadar() {
  if (!radarImg || !radarImg.complete || !radarImg.naturalWidth) return
  const W = CANVAS, H = CANVAS, c = document.createElement('canvas'); c.width = W; c.height = H
  const cx = c.getContext('2d', { willReadFrequently: true }); cx.drawImage(radarImg, 0, 0, W, H)
  radarPixels = cx.getImageData(0, 0, W, H)
}
// 预热：当前 tick ±2（整数 tick 请求）
function warm() {
  if (!engine) return
  const t0 = Math.floor((currentTime.value + 1e-4) / 0.1)
  for (const d of [-2, -1, 0, 1, 2]) engine.requestFrame(t0 + d)
}

const initialTime = () => {
  const s = props.sample; if (!s) return 0
  const D = s.round.max_duration || 0
  const kills = (s.events?.kills || []).map((k) => Number(k.time)).filter(Number.isFinite)
  let best = 0, bs = -1
  for (let t = 0; t <= D; t += 0.5) {
    let regions = 0; for (const a of s.areas || []) if ((a.segments?.A || []).some((x) => t >= x.start && t <= x.end) || (a.segments?.B || []).some((x) => t >= x.start && t <= x.end)) regions++
    const b = kills.filter((k) => t >= k && t <= k + 3).length
    const sc = regions * 100 + b * 500
    if (sc > bs) { bs = sc; best = t }
  }
  return Math.round(best * 100) / 100
}

// ---- 时间轴 ----
function animate(ts) {
  if (!playing.value) return
  if (!lastFrame) lastFrame = ts
  const dt = (ts - lastFrame) / 1000; lastFrame = ts
  let next = currentTime.value + dt * speed.value
  if (next >= duration.value) { next = duration.value; playing.value = false }
  currentTime.value = next
  scheduleRender()
  if (playing.value) rafId = requestAnimationFrame(animate)
}
function togglePlay() {
  if (reduced.value) { playing.value = false; currentTime.value = currentTime.value === 0 ? duration.value : 0; scheduleRender(); return }
  if (currentTime.value >= duration.value) currentTime.value = 0
  playing.value = !playing.value; lastFrame = 0
  if (playing.value) rafId = requestAnimationFrame(animate)
  else if (rafId) cancelAnimationFrame(rafId)
}
function seek(e) { const v = Number(e.target.value); if (Number.isFinite(v)) { currentTime.value = Math.min(Math.max(0, v), duration.value); scheduleRender() } }
function setSpeed(v) { speed.value = v }
function toEnd() { currentTime.value = duration.value; playing.value = false; scheduleRender() }
let renderQueued = false
function scheduleRender() { if (renderQueued) return; renderQueued = true; requestAnimationFrame(() => { renderQueued = false; render() }) }

// ---- 渲染（Canvas 2D；端点纹理交叉淡入淡出；产品风格克制 alpha）----
function render() {
  const cv = canvasRef.value; if (!cv) return
  const ctx = cv.getContext('2d', { willReadFrequently: true }); const W = cv.width, H = cv.height
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#0a0f15'; ctx.fillRect(0, 0, W, H)
  if (radarPixels) { ctx.putImageData(radarPixels, 0, 0) } else if (radarImg && radarImg.complete) { ctx.drawImage(radarImg, 0, 0, W, H) }
  const t = currentTime.value
  const eng = engine; if (!eng) return
  const toPx = (wx, wy) => { const r = eng.toRadar(wx, wy); return [r.rx * W, r.ry * H] }
  // E6D-PERF：玩家真值每帧只算一次（含死亡视觉），不再在 alive/deathX/aliveCount 重复全量扫描（players 块 ~10ms 的主因）。
  const pvs = eng.playerVisualAt(t)
  let aliveCount = 0
  for (const p of pvs) if (p.alive) aliveCount++
  const deathTick = eng.deathTick, deathVisual = eng.deathVisual
  const t0 = performance.now()
  const { tick0, tick1, frac, ep0, ep1 } = eng.ensureBracket(t)
  eng.prebuildAhead([tick0 + 1, tick0 + 2, tick0 + 3, tick0 + 4, tick0 + 5]) // 摊薄 10Hz 端点构建并前瞻下一 bracket（tick0+1），避免边界帧同步重建掉帧
  const tBuild = performance.now()
  if (ep0 && ep1) {
    const comp = engine.compositeField(ep0, ep1, frac, compositeCanvas)
    const tComposite = performance.now()
    if (layers.scalar) { ctx.imageSmoothingEnabled = true; ctx.globalAlpha = 1; ctx.drawImage(compositeCanvas, 0, 0, W, H) }
    if (layers.front) { drawFrontier(ctx, W, H, ep0.frontier, 1 - frac); drawFrontier(ctx, W, H, ep1.frontier, frac) }
    const tField = performance.now()
    engine.recordPhase({ buildMs: +(tBuild - t0).toFixed(2), compositeMs: +(tComposite - tBuild).toFixed(2), drawMs: +(tField - tComposite).toFixed(2) })
    engine.tickFieldRender()
    ready = true
    publish(t, tick0, tick1, frac, ep0, ep1, comp, aliveCount)
    if (mcReplay) mcReplay.lastFrame = { buildMs: +(tBuild - t0).toFixed(2), compositeMs: +(tComposite - tBuild).toFixed(2), fieldDrawMs: +(tField - tComposite).toFixed(2), totalMs: +(tField - t0).toFixed(2) }
  } else if (mcReplay) {
    mcReplay.lastFrame = { buildMs: +(tBuild - t0).toFixed(2), compositeMs: 0, fieldDrawMs: 0, totalMs: +(tBuild - t0).toFixed(2), note: 'no-endpoints' }
  }
  const tOverlay0 = performance.now()
  // 玩家/事件/击杀/补枪/闪/死亡X（同时间轴每帧插值）
  if (layers.players) {
    const tLive0 = performance.now()
    for (const p of pvs) {
      if (!p.alive) continue
      const [px, py] = toPx(p.wx, p.wy)
      ctx.beginPath(); ctx.arc(px, py, 6, 0, Math.PI * 2)
      ctx.fillStyle = p.side === 'T' ? '#f0a020' : '#5b9dd9'; ctx.fill()
      ctx.lineWidth = 2; ctx.strokeStyle = '#edf1f6'; ctx.stroke()
    }
    const tLive1 = performance.now()
    if (layers.deathX) {
      for (const p of pvs) {
        if (p.alive) continue
        const dt = deathTick ? deathTick(p.uid) : null; if (dt == null || t < dt) continue
        const pv = deathVisual ? deathVisual(p.uid) : null
        if (!pv) continue
        const [px, py] = toPx(pv.wx, pv.wy)
        const fade = Math.max(0.15, 1 - (t - dt) / 4)
        ctx.strokeStyle = `rgba(229,100,93,${fade})`; ctx.lineWidth = 3; ctx.beginPath()
        ctx.moveTo(px - 6, py - 6); ctx.lineTo(px + 6, py + 6); ctx.moveTo(px + 6, py - 6); ctx.lineTo(px - 6, py + 6); ctx.stroke()
      }
    }
    const tLive2 = performance.now()
    if (mcReplay && mcReplay.lastFrame) { mcReplay.lastFrame.aliveMs = +(tLive1 - tLive0).toFixed(2); mcReplay.lastFrame.deathXMs = +(tLive2 - tLive1).toFixed(2) }
  }
  const tPlayers = performance.now()
  if (layers.events || layers.kills) {
    for (const k of props.sample?.events?.kills || []) {
      const kt = Number(k.time); if (!Number.isFinite(kt) || kt > t) continue
      const v = eng.playerByUid(k.victim)
      if (!v) continue
      const vp = samplePoint(v.points, kt); if (!vp) continue
      const w = eng.toWorld(vp.rx, vp.ry); const [px, py] = toPx(w.x, w.y)
      const fade = Math.max(0, 1 - (t - kt) / 4); if (fade <= 0) continue
      ctx.globalAlpha = 0.25 * fade; ctx.fillStyle = '#e5655d'; ctx.beginPath(); ctx.arc(px, py, 15, 0, Math.PI * 2); ctx.fill()
      ctx.globalAlpha = fade; ctx.fillStyle = '#ffd24a'; ctx.font = 'bold 9px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText((k.weapon || '').replace(/^weapon_/, '').slice(0, 3).toUpperCase(), px, py); ctx.globalAlpha = 1; ctx.textAlign = 'left'
    }
  }
  const tKills = performance.now()
  if (layers.flash) {
    for (const b of props.sample?.events?.blinds || []) {
      const bt = Number(b.time); if (!Number.isFinite(bt) || Math.abs(t - bt) > 0.3) continue
      const v = eng.playerByUid(b.victim)
      if (!v) continue
      const pt = samplePoint(v.points, bt); if (!pt) continue
      const w = eng.toWorld(pt.rx, pt.ry); const [px, py] = toPx(w.x, w.y)
      ctx.fillStyle = 'rgba(255,255,255,0.4)'; ctx.beginPath(); ctx.arc(px, py, 20, 0, Math.PI * 2); ctx.fill()
    }
  }
  const tEnd = performance.now()
  if (mcReplay && mcReplay.lastFrame && mcReplay.lastFrame.totalMs != null) { mcReplay.lastFrame.overlayMs = +(tEnd - tOverlay0).toFixed(2); mcReplay.lastFrame.playersMs = +(tPlayers - tOverlay0).toFixed(2); mcReplay.lastFrame.killsMs = +(tKills - tPlayers).toFixed(2); mcReplay.lastFrame.flashMs = +(tEnd - tKills).toFixed(2); mcReplay.lastFrame.totalMs = +(tEnd - t0).toFixed(2) }
}
function samplePoint(points, t) {
  if (!points || !points.length) return null
  if (t <= points[0].t) return points[0]
  const last = points[points.length - 1]; if (t >= last.t) return last
  for (let i = 0; i < points.length - 1; i++) { const a = points[i], b = points[i + 1]; if (a.t <= t && t <= b.t) { const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0; return { rx: a.rx + (b.rx - a.rx) * f, ry: a.ry + (b.ry - a.ry) * f } } }
  return null
}
function drawFrontier(ctx, W, H, fs, alphaMul) {
  if (!fs || !engine) return
  const px = (x, y) => { const r = engine.toRadar(x, y); return [r.rx * W, r.ry * H] }
  ctx.save(); ctx.lineCap = 'round'; ctx.lineJoin = 'round'
  for (const line of fs.lines) {
    const [x0, y0] = px(line.pts[0].x, line.pts[0].y)
    ctx.beginPath(); ctx.moveTo(x0, y0)
    for (let i = 1; i < line.pts.length; i++) { const [x, y] = px(line.pts[i].x, line.pts[i].y); ctx.lineTo(x, y) }
    if (line.active) {
      ctx.strokeStyle = 'rgba(0,0,0,0.7)'; ctx.globalAlpha = 0.7 * alphaMul; ctx.lineWidth = 7; ctx.stroke()
      ctx.strokeStyle = 'rgba(91,157,217,0.5)'; ctx.globalAlpha = 0.5 * alphaMul; ctx.lineWidth = 4; ctx.stroke()
      ctx.strokeStyle = 'rgba(230,165,74,0.5)'; ctx.globalAlpha = 0.5 * alphaMul; ctx.lineWidth = 4; ctx.stroke()
      ctx.strokeStyle = 'rgba(120,220,255,0.95)'; ctx.globalAlpha = 0.95 * alphaMul; ctx.lineWidth = 3; ctx.stroke()
    } else { ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(140,160,180,0.42)'; ctx.globalAlpha = 0.42 * alphaMul; ctx.lineWidth = 2; ctx.stroke(); ctx.setLineDash([]) }
  }
  ctx.globalAlpha = 1; ctx.restore()
}
let mcReplay = null
let hashesArr = [0, 0], ticksArr = [0, 0], sourcesArr = ['worker', 'worker']
function publish(t, tick0, tick1, frac, ep0, ep1, comp, aliveCount) {
  const eng = engine
  if (typeof window === 'undefined') return
  if (!mcReplay) {
    mcReplay = window.__mcReplay = {
      awaitExactAt: (tt, to) => eng.awaitExactAt(tt, to), captureExactSnapshot: (tt, to) => eng.captureExactSnapshot(tt, to),
      toggle: (layer, on) => { layers[layer] = !!on; scheduleRender() }, getLayer: (layer) => !!layers[layer],
      buildPhaseMax: {}, phaseMax: {}, requestTick: null,
    }
  }
  // 原地改写（避免每帧新建对象/数组 → GC 尖峰）
  mcReplay.currentTime = t; mcReplay.tick0 = tick0; mcReplay.tick1 = tick1; mcReplay.frac = frac
  mcReplay.states = ep0.states; mcReplay.alive = aliveCount != null ? aliveCount : eng.aliveCountAt(t)
  hashesArr[0] = ep0.hash; hashesArr[1] = ep1.hash; mcReplay.hashes = hashesArr; mcReplay.frameHash = ep0.hash; mcReplay.fieldChecksum = comp.fieldChecksum
  mcReplay.modelExact = eng.modelExactNow(tick0, tick1); ticksArr[0] = tick0; ticksArr[1] = tick1; mcReplay.requestedTicks = ticksArr; mcReplay.resolvedTicks = ticksArr; mcReplay.frameSources = sourcesArr
  mcReplay.areas = props.nav.areas.length; mcReplay.gridN = GRID_N; mcReplay.demoSha = props.sample?.content_hash || mapName.value || 'demo'
  mcReplay.map = mapName.value; mcReplay.areaCount = areaCount.value
  mcReplay.renderEpoch = (mcReplay.renderEpoch || 0) + 1
  const met = eng.metrics()
  mcReplay.fieldRenderHz = met.fieldRenderHz; mcReplay.endpointBuildCount = met.endpointBuildCount; mcReplay.hotPathFullScans = met.hotPathFullScans
  mcReplay.req = met.req; mcReplay.uniqueTicks = met.uniqueTicks; mcReplay.cacheHits = met.cacheHits
  mcReplay.maxFramePhase = met.maxFramePhase; mcReplay.phaseMax = met.phaseMax; mcReplay.buildPhaseMax = met.buildPhaseMax
  mcReplay.workerReady = met.workerReady; mcReplay.allocCounters = met.allocCounters
}
function pauseAt(t) { currentTime.value = t; playing.value = false; scheduleRender() }
onBeforeUnmount(() => { if (rafId) cancelAnimationFrame(rafId); if (engine) engine.dispose() })
defineExpose({ pauseAt, getEngine: () => engine, getCurrent: () => currentTime.value })
</script>

<template>
  <div class="mcr" data-testid="mcr-canvas-wrap">
    <div class="mcr-bar">
      <button type="button" class="mcr-play" @click="togglePlay" :aria-label="playing ? '暂停' : '播放'">{{ playing ? '暂停' : '播放' }}</button>
      <button type="button" class="mcr-btn" @click="toEnd">结束</button>
      <div class="mcr-seg" role="group" aria-label="播放速度">
        <button v-for="s in [0.5,1,2,4]" :key="s" type="button" :class="{ on: speed === s }" @click="setSpeed(s)">{{ s }}x</button>
      </div>
      <input class="mcr-tl" type="range" min="0" :max="duration || 1" step="0.01" :value="currentTime" @input="seek" :aria-label="'时间轴'" />
      <span class="mcr-time">{{ clock }} / {{ String(Math.floor(duration / 60)).padStart(2, '0') }}:{{ String(Math.round(duration % 60)).padStart(2, '0') }}</span>
      <span v-if="reduced" class="mcr-hint">reduced-motion：静态</span>
    </div>
    <div class="mcr-layers" role="group" aria-label="图层">
      <label><input type="checkbox" v-model="layers.scalar" @change="scheduleRender" />控制场(雾)</label>
      <label><input type="checkbox" v-model="layers.front" @change="scheduleRender" />控制前线</label>
      <label><input type="checkbox" v-model="layers.players" @change="scheduleRender" />玩家</label>
      <label><input type="checkbox" v-model="layers.events" @change="scheduleRender" />击杀/事件</label>
      <label><input type="checkbox" v-model="layers.flash" @change="scheduleRender" />闪</label>
      <label><input type="checkbox" v-model="layers.deathX" @change="scheduleRender" />死亡X</label>
    </div>
    <canvas ref="canvasRef" class="mcr-canvas" :width="CANVAS" :height="CANVAS" data-testid="mcr-canvas" data-ready="ready ? 'ready' : ''"></canvas>
    <p class="mcr-note">真实 {{ mapLabel(mapName) || mapName }} × {{ mapName }} NavMesh（{{ areaCount }} area）连续控制场 · 玩家/击杀/补枪/闪/死亡X 同时间轴 · 可玩区裁剪</p>
  </div>
</template>

<style scoped>
.mcr { border: 1px solid var(--border-strong, #313a45); background: #0a0f15; color: var(--text-primary, #dbe2ec); max-width: 900px; }
.mcr-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 10px 12px; border-bottom: 1px solid var(--border, #313a45); }
.mcr-play, .mcr-btn { min-height: 44px; min-width: 44px; border: 1px solid var(--border-strong, #313a45); background: #0d1218; color: #e7ecf2; cursor: pointer; }
.mcr-play { min-width: 60px; }
.mcr-seg { display: flex; gap: 3px; }
.mcr-seg button { min-width: 44px; min-height: 44px; border: 1px solid var(--border, #313a45); background: #0d1218; color: #aab4c2; cursor: pointer; }
.mcr-seg button.on { color: #f0a020; border-color: #f0a020; }
.mcr-tl { flex: 1; min-width: 120px; accent-color: #f0a020; }
.mcr-time { font-variant-numeric: tabular-nums; font-size: 12px; color: #aab4c2; white-space: nowrap; }
.mcr-hint { color: #f0a020; font-size: 12px; }
.mcr-layers { display: flex; gap: 10px; flex-wrap: wrap; padding: 8px 12px; border-bottom: 1px solid var(--border, #313a45); font-size: 12px; color: #aab4c2; }
.mcr-layers label { display: inline-flex; align-items: center; gap: 4px; min-height: 44px; min-width: 44px; cursor: pointer; }
.mcr-canvas { display: block; width: 100%; max-width: 720px; margin: 0 auto; aspect-ratio: 1 / 1; border: 1px solid #313a45; background: #080b0f; will-change: transform; transform: translateZ(0); contain: layout paint; }
.mcr-note { margin: 0; padding: 8px 12px; color: #7d8c9e; font-size: 11px; }
</style>
