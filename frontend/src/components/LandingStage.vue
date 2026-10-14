<script setup>
// 6R21F-F1：真实数据驱动的战术控制室舞台（首页 P0，视觉重构）。
// 数据源：frontend/public/demo/6r21-round-sample.json（demo19 权威 tactical artifact：匿名/版本化/可审计）。
// 所有点位/区域均经统一世界→雷达变换并裁剪到可玩区；无手绘路径、无越界、不伪造数据。
// F1 关键改动：
//  - 首屏立即 seek 到「真实叙事峰值」（watch immediate），不再打开在 00:00 空场帧。
//  - 区域控制直接复用产品真值管线：官方 NavMesh 上计算连续 scalar field，并以 Canvas 单纹理绘制；
//    高置信 frontier 为实线，数据不足的 geodesic bisector 为明确虚线。没有 per-area 色块或 DOM texel。
//    Canvas / frontier / 玩家 / 击杀共用 sample.map 世界→雷达变换与同一时间轴，并由 NavMesh mask 裁剪。
//  - 保留全部数据派生逻辑（玩家插值/死亡/区域状态机/击杀事件层），仅重做视觉质感与信息层级。
//  - reduced-motion：确定性静态快照（无连续 rAF）。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { MorphIcon } from 'morphicons/vue'
import { ChevronDown, FastForward, Pause, Play, SkipBack, X } from 'lucide'
import { createPipeline } from '../map-control/pipeline.js'
import { renderScalarField } from '../map-control/fieldTexture.js'

const props = defineProps({
  sample: { type: Object, default: null },
  autoplay: { type: Boolean, default: false },
})

const duration = computed(() => (props.sample?.round?.max_duration ?? 0))
const radarUrl = computed(() => `/api/maps/${encodeURIComponent(props.sample?.map?.name || 'de_mirage')}/radar`)
const currentTime = ref(0)
const playing = ref(false)
const speed = ref(1)
const eventOpen = ref(false)
const prefersReduced = ref(false)
const fieldCanvas = ref(null)
const fieldStatus = ref('loading')
const frontierPaths = ref({ active: [], low: [] })
let rafId = 0
let lastFrame = 0
let fieldRafId = 0
let fieldPipeline = null
let lastFieldTick = -1
let autoplayTimer = 0
let userControlledPlayback = false

const AUTOPLAY_DELAY_MS = 3200

const FIELD_GRID_N = 128
const FIELD_CFG = Object.freeze({
  sigma: 550,
  neutralThreshold: 0.22,
  contestRatio: 0.2,
  maxPropagationDistance: 2200,
  frontierBand: 0.05,
})

onMounted(() => {
  prefersReduced.value = typeof window !== 'undefined' &&
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  loadControlField()
})

async function loadControlField() {
  fieldStatus.value = 'loading'
  try {
    const mapName = props.sample?.map?.name || 'de_mirage'
    const response = await fetch(`/api/maps/${encodeURIComponent(mapName)}/navmesh`)
    if (!response.ok) throw new Error(`navmesh ${response.status}`)
    const nav = await response.json()
    if (!Array.isArray(nav?.areas) || !nav.areas.length || !props.sample) throw new Error('navmesh empty')
    fieldPipeline = createPipeline({ nav, sample: props.sample, cfg: FIELD_CFG, opts: { gridN: FIELD_GRID_N, cacheLimit: 28 } })
    fieldStatus.value = 'ready'
    lastFieldTick = -1
    scheduleFieldRender(true)
    scheduleAutoplay()
  } catch (error) {
    fieldPipeline = null
    frontierPaths.value = { active: [], low: [] }
    fieldStatus.value = 'unavailable'
  }
}

function contourPath(line) {
  if (!line?.length) return ''
  return line.map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(2)} ${point[1].toFixed(2)}`).join(' ')
}

function renderControlField() {
  if (!fieldPipeline || !fieldCanvas.value) return
  const canvas = fieldCanvas.value
  if (canvas.width !== FIELD_GRID_N) canvas.width = FIELD_GRID_N
  if (canvas.height !== FIELD_GRID_N) canvas.height = FIELD_GRID_N
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const contour = fieldPipeline.contourAt(currentTime.value)
  renderScalarField(ctx, FIELD_GRID_N, { cv: contour.scv, mag: contour.grid.mag, mask: contour.grid.mask }, FIELD_CFG, 0)
  const live = fieldPipeline.playersAt(currentTime.value).filter((player) => player.alive)
  const hasCt = live.some((player) => player.side === 'CT')
  const hasT = live.some((player) => player.side === 'T')
  frontierPaths.value = {
    // 只有双方都仍有存活控制源时，零等值线才具有“交战前线”语义。
    active: hasCt && hasT ? contour.activeLines.map(contourPath).filter(Boolean) : [],
    low: hasCt && hasT ? contour.lowLines.map(contourPath).filter(Boolean) : [],
  }
}

function scheduleAutoplay() {
  if (!props.autoplay || prefersReduced.value || playing.value || autoplayTimer || userControlledPlayback) return
  autoplayTimer = window.setTimeout(() => {
    autoplayTimer = 0
    if (userControlledPlayback || !props.sample || document.visibilityState === 'hidden' || currentTime.value >= duration.value) return
    playing.value = true
    lastFrame = 0
    rafId = requestAnimationFrame(animate)
  }, AUTOPLAY_DELAY_MS)
}

function scheduleFieldRender(force = false) {
  if (!fieldPipeline) return
  // 10 Hz 模型帧，与真值管线 modelH=0.1 对齐；播放 rAF 不做重复全图扫描。
  const tick = Math.floor((currentTime.value + 1e-4) * 10)
  if (!force && tick === lastFieldTick) return
  lastFieldTick = tick
  if (fieldRafId) cancelAnimationFrame(fieldRafId)
  fieldRafId = requestAnimationFrame(() => {
    fieldRafId = 0
    renderControlField()
  })
}

const SPEEDS = [0.5, 1, 2, 4]

const clock = computed(() => {
  const s = Math.max(0, Math.min(duration.value, currentTime.value))
  const m = Math.floor(s / 60)
  const ss = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
})
const roundClock = computed(() => {
  const remain = Math.max(0, duration.value - currentTime.value)
  const m = Math.floor(remain / 60)
  const ss = Math.round(remain % 60)
  return { m: String(m).padStart(2, '0'), s: String(ss).padStart(2, '0') }
})

const roundLabel = computed(() => {
  if (!props.sample) return ''
  const sb = props.sample.round.team_side_map || {}
  const ct = sb.A || 'CT'
  const t = sb.B || 'T'
  const winner = props.sample.round.winner_team === 'A' ? ct : (props.sample.round.winner_team === 'B' ? t : '')
  return { ct, t, winner }
})

// ---- 播放 / 暂停 / seek / 倍速 ----
function animate(ts) {
  if (!playing.value) return
  if (!lastFrame) lastFrame = ts
  const dt = (ts - lastFrame) / 1000
  lastFrame = ts
  let next = currentTime.value + dt * speed.value
  if (next >= duration.value) {
    next = duration.value
    playing.value = false
    currentTime.value = next
    return
  }
  currentTime.value = next
  rafId = requestAnimationFrame(animate)
}

function togglePlay() {
  if (!props.sample) return
  userControlledPlayback = true
  if (autoplayTimer) {
    window.clearTimeout(autoplayTimer)
    autoplayTimer = 0
  }
  if (currentTime.value >= duration.value) currentTime.value = 0
  if (prefersReduced.value) {
    // reduced-motion：不做连续动画，播放=跳到末尾快照，暂停=回到起点快照（确定性静态当前态势）
    playing.value = false
    currentTime.value = currentTime.value === 0 ? duration.value : 0
    return
  }
  playing.value = !playing.value
  lastFrame = 0
  if (playing.value) rafId = requestAnimationFrame(animate)
  else cancelAnimationFrame(rafId)
}

function seek(event) {
  if (!props.sample) return
  const rect = event.currentTarget.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
  currentTime.value = ratio * duration.value
}

function setSpeed(s) { speed.value = s }

function prevEvent() { seekToRelative(-1) }
function nextEvent() { seekToRelative(1) }
function seekToRelative(dir) {
  if (!props.sample) return
  const times = allEventTimes.value
  if (!times.length) return
  const t = currentTime.value
  const target = dir < 0
    ? [...times].reverse().find((x) => x < t - 0.2)
    : times.find((x) => x > t + 0.2)
  currentTime.value = target ?? (dir < 0 ? 0 : duration.value)
}

// ---- 数据派生 ----
function samplePoints(points, t) {
  if (!points || !points.length) return null
  if (t <= points[0].t) return { x: points[0].rx, y: points[0].ry, alive: points[0].alive }
  const last = points[points.length - 1]
  if (t >= last.t) return { x: last.rx, y: last.ry, alive: last.alive }
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1]
    if (a.t <= t && t <= b.t) {
      const span = b.t - a.t
      const f = span > 0 ? (t - a.t) / span : 0
      return { x: a.rx + (b.rx - a.rx) * f, y: a.ry + (b.ry - a.ry) * f, alive: t >= b.t ? b.alive : a.alive }
    }
  }
  return null
}

function deathTime(uid, points) {
  const eventTime = (props.sample?.events?.kills || [])
    .filter((kill) => kill.victim === uid && Number.isFinite(Number(kill.time)))
    .map((kill) => Number(kill.time))
    .sort((a, b) => a - b)[0]
  if (eventTime != null) return eventTime
  if (!points || !points.length) return null
  const firstDead = points.find((point) => point.alive === false)
  return firstDead ? Number(firstDead.t) : null
}

const players = computed(() => {
  const t = currentTime.value
  const out = []
  for (const p of props.sample?.players ?? []) {
    const pos = samplePoints(p.points, t)
    if (!pos) continue
    const dt = deathTime(p.uid, p.points)
    out.push({
      uid: p.uid, side: p.side, team: p.team, outcome: p.outcome,
      num: p.uid.replace(/^C?T/, ''),
      x: pos.x, y: pos.y,
      alive: pos.alive && !(dt != null && t >= dt),
      dead: pos.alive === false || (dt != null && t >= dt),
      justDied: dt != null && Math.abs(t - dt) < 0.5,
    })
  }
  return out
})

const sampleSide = computed(() => {
  const sb = props.sample?.round?.team_side_map || {}
  return { A: sb.A || 'CT', B: sb.B || 'T' }
})

// 某区域在 t 时刻的展示状态：contested > controlled > occupied；influence/unknown 不绘制（诚实）。
function regionDisplay(a, t) {
  const segA = (a.segments.A || []).find((s) => t >= s.start && t <= s.end)
  const segB = (a.segments.B || []).find((s) => t >= s.start && t <= s.end)
  const sideState = {}
  if (segA) sideState[sampleSide.value.A] = segA.state
  if (segB) sideState[sampleSide.value.B] = segB.state
  const ct = sideState.CT, tt = sideState.T
  if (ct === 'contested' || tt === 'contested') return { kind: 'contested', label: '争夺' }
  if (ct === 'controlled') return { kind: 'ct', label: 'CT 控制' }
  if (tt === 'controlled') return { kind: 't', label: 'T 控制' }
  if (ct === 'occupied') return { kind: 'ct', label: 'CT 占据' }
  if (tt === 'occupied') return { kind: 't', label: 'T 占据' }
  return null
}

// 默认 seek 时刻 = 真实「叙事峰值」：最大化同时可见区域数，其次落在"击杀后 3s 内"的事件张力窗口。
// 确定性（纯数据扫描，无随机）。→ 首屏不再呈现空场，而是打开在真实最丰富的一帧。
const initialTime = computed(() => {
  const s = props.sample
  if (!s) return 0
  const D = s.round?.max_duration || 0
  const kills = (s.events?.kills || []).map((k) => Number(k.time)).filter(Number.isFinite)
  let bestT = 0, bestScore = -Infinity
  for (let t = 0; t <= D; t += 0.25) {
    let regions = 0
    for (const a of s.areas) if (regionDisplay(a, t)) regions++
    let bonus = 0
    for (const kt of kills) if (t >= kt && t <= kt + 3) bonus += 1
    const score = regions * 100 + bonus * 500
    if (score > bestScore) { bestScore = score; bestT = t }
  }
  return Math.round(bestT * 100) / 100
})
// F1：immediate，使首屏第一次渲染（sample 已就绪）即 seek 到叙事峰值。
watch(() => props.sample, (s) => {
  if (s && currentTime.value === 0) currentTime.value = initialTime.value
}, { immediate: true })

// 当前激活区域的中文地点标签（用真实 place_cn，放在真实质心，不杜撰）；用于地图可读性。
const regionLabels = computed(() => {
  const t = currentTime.value
  const out = []
  for (const a of props.sample?.areas ?? []) {
    if (!a.polygon || a.polygon.length < 3) continue
    const disp = regionDisplay(a, t)
    if (!disp) continue
    const name = (a.place_cn || '').trim()
    const cx = a.centroid?.rx, cy = a.centroid?.ry
    if (!name || cx == null || cy == null) continue
    out.push({ key: a.place_key, state: disp.kind, name, x: cx, y: cy })
  }
  return out
})

// 区域骨架：全部真实区域多边形（含当前未控制者）的虚线轮廓，给地图"战术网格"结构（真实几何，不暗示当前控制）。
const zoneSkeleton = computed(() => {
  if (!props.sample) return []
  const out = []
  for (const a of props.sample.areas ?? []) {
    if (!a.polygon || a.polygon.length < 3) continue
    out.push({ key: a.place_key, points: a.polygon.map((pt) => `${pt.rx},${pt.ry}`).join(' ') })
  }
  return out
})

// 真实击杀事件层：击杀 ≤ 当前时刻 → 受害/攻击位置（真实采样点插值），编号 + 攻击→受害引线。
const eventMarkers = computed(() => {
  const t = currentTime.value
  const s = props.sample
  if (!s) return []
  const out = []
  let n = 0
  for (const k of (s.events?.kills || [])) {
    const kt = Number(k.time)
    if (!Number.isFinite(kt) || kt > t) continue
    n++
    const victim = s.players.find((p) => p.uid === k.victim)
    const attacker = s.players.find((p) => p.uid === k.attacker)
    const vp = victim && samplePoints(victim.points, kt)
    const ap = attacker && samplePoints(attacker.points, kt)
    if (!vp) continue
    out.push({
      num: n, time: kt, weapon: (k.weapon || '').replace(/^weapon_/, ''),
      victim: k.victim, attacker: k.attacker,
      victimTeam: victim?.side === 'T' ? 't' : 'ct',
      attackerTeam: attacker?.side === 'T' ? 't' : 'ct',
      vx: vp.x, vy: vp.y,
      ax: ap ? ap.x : null, ay: ap ? ap.y : null,
    })
  }
  return out
})

const allEventTimes = computed(() => {
  const s = props.sample
  if (!s) return []
  const times = []
  for (const k of s.events?.kills || []) if (k.time != null) times.push(k.time)
  for (const tr of s.events?.trades || []) if (tr.time != null) times.push(tr.time)
  return [...new Set(times)].sort((a, b) => a - b)
})

const recentKills = computed(() => {
  const t = currentTime.value
  const s = props.sample
  if (!s) return []
  const all = []
  for (const k of s.events?.kills || []) if (k.time != null && k.time <= t) all.push({ ...k, kind: 'kill' })
  for (const tr of s.events?.trades || []) if (tr.time != null && tr.time <= t) all.push({ ...tr, kind: 'trade' })
  return all.sort((a, b) => b.time - a.time).slice(0, 3)
})

const currentEvidence = computed(() => {
  const t = currentTime.value
  const s = props.sample
  if (!s) return null
  const evs = (s.evidence || []).filter((e) => e.time != null)
  if (!evs.length) return null
  let best = null, bd = Infinity
  for (const e of evs) { const d = Math.abs(e.time - t); if (d < bd) { bd = d; best = e } }
  if (bd <= 8) return best
  const next = evs.filter((e) => e.time >= t).sort((a, b) => a.time - b.time)[0]
  return next || evs[evs.length - 1]
})

function fmtSec(v) {
  if (v == null) return '00:00'
  const s = Math.max(0, Math.floor(v))
  const m = Math.floor(s / 60)
  const ss = s % 60
  return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

const progress = computed(() => duration.value ? Math.min(1, currentTime.value / duration.value) : 0)
const aliveSummary = computed(() => {
  const live = players.value.filter((player) => player.alive)
  return {
    ct: live.filter((player) => player.side === 'CT').length,
    t: live.filter((player) => player.side === 'T').length,
  }
})

watch(currentTime, () => scheduleFieldRender())

onBeforeUnmount(() => {
  cancelAnimationFrame(rafId)
  if (fieldRafId) cancelAnimationFrame(fieldRafId)
  if (autoplayTimer) window.clearTimeout(autoplayTimer)
})
</script>

<template>
  <div class="landing-stage" data-testid="landing-replay-stage" v-if="sample">
    <div class="stage-topbar">
      <div class="team ct"><span class="team-badge ct">CT</span><b>{{ roundLabel.ct }}</b></div>
      <div class="round-clock">
        <strong class="clock-num">{{ clock }}</strong>
        <span>第 {{ sample.round.number }} 回合 · 真实片段 · {{ sample.map.name.replace('de_', '') }} 地图</span>
        <span class="live-driver" data-testid="landing-live-driver">
          存活 CT {{ aliveSummary.ct }} · T {{ aliveSummary.t }} · 前线仅由存活者生成
        </span>
      </div>
      <div class="team t"><b>{{ roundLabel.t }}</b><span class="team-badge t">T</span></div>
    </div>

    <div class="engine">
      <img :src="radarUrl" alt="CS2 地图雷达结构底图" class="radar" />
      <canvas
        ref="fieldCanvas"
        class="control-field"
        :width="FIELD_GRID_N"
        :height="FIELD_GRID_N"
        data-testid="landing-control-field"
        aria-hidden="true"
      ></canvas>
      <div class="grid-lines" aria-hidden="true"></div>

      <svg class="area-layer" :viewBox="`0 0 ${FIELD_GRID_N} ${FIELD_GRID_N}`" preserveAspectRatio="none" aria-hidden="true">
        <!-- 区域骨架：真实区域几何的虚线轮廓（战术网格），不暗示当前控制 -->
        <polygon
          v-for="z in zoneSkeleton"
          :key="'z' + z.key"
          :points="z.points"
          class="zone-skel"
          :transform="`scale(${FIELD_GRID_N})`"
        ></polygon>
        <!-- 同一 NavMesh scalar field 的零等值线：实线=高置信，虚线=低置信 geodesic bisector。 -->
        <path v-for="(path, i) in frontierPaths.active" :key="`fa-${i}`" :d="path" class="control-frontier active"></path>
        <path v-for="(path, i) in frontierPaths.low" :key="`fl-${i}`" :d="path" class="control-frontier low"></path>
        <!-- 击杀事件层：攻击→受害 引线（真实位置插值，队色，虚线） -->
        <line
          v-for="m in eventMarkers"
          :key="'l' + m.time + m.victim"
          v-show="m.ax != null && m.ay != null"
          class="kill-line"
          :class="m.attackerTeam"
          :x1="m.ax * FIELD_GRID_N" :y1="m.ay * FIELD_GRID_N" :x2="m.vx * FIELD_GRID_N" :y2="m.vy * FIELD_GRID_N"
        ></line>
      </svg>

      <!-- 死亡位置来自同一击杀事件时间轴；红 X 从死亡时刻持续到回合结束。 -->
      <div
        v-for="m in eventMarkers"
        :key="'e' + m.time + m.victim"
        class="death-marker"
        :class="m.victimTeam"
        :style="{ left: `${m.vx * 100}%`, top: `${m.vy * 100}%` }"
        :aria-label="`${m.time.toFixed(1)}s ${m.attacker} 击杀 ${m.victim}（${m.weapon}）`"
        data-testid="landing-death-x"
      >
        <MorphIcon :icon="X" :size="23" :stroke-width="3.1" reduced-motion="user" aria-hidden="true" />
        <span class="death-event-num">{{ m.num }}</span>
      </div>

      <div
        v-for="p in players"
        :key="p.uid"
        class="actor"
        :class="[p.side === 'T' ? 't' : 'ct', { dead: p.dead }]"
        :style="{ left: `${p.x * 100}%`, top: `${p.y * 100}%` }"
        :aria-label="`${p.side === 'T' ? '进攻方' : '防守方'} ${p.uid}`"
      >
        <span class="actor-id">{{ p.num }}</span>
      </div>
      <span v-for="p in players.filter((x) => x.justDied)" :key="'k' + p.uid" class="kill-ring" :style="{ left: `${p.x * 100}%`, top: `${p.y * 100}%` }" aria-hidden="true"></span>

      <div class="zone-labels" aria-hidden="true">
        <span
          v-for="l in regionLabels"
          :key="l.key"
          class="zone-label"
          :class="l.state"
          :style="{ left: `${l.x * 100}%`, top: `${l.y * 100}%` }"
        >{{ l.name }}</span>
      </div>

      <aside class="kill-feed" aria-label="击杀播报" v-if="recentKills.length">
        <p class="feed-map">Mirage · 第 {{ sample.round.number }} 回合</p>
        <p v-for="(k, index) in recentKills" :key="`${k.time}-${k.kind}-${k.killer || k.trader || 'event'}-${k.victim || ''}-${index}`" class="feed-row">
          <template v-if="k.kind === 'trade'">
            <span class="t-text">{{ k.trader }}</span><b>补枪</b><span class="ct-text">{{ k.killer }}</span>
          </template>
          <template v-else>
            <span :class="k.attacker_team === 'A' ? 'ct-text' : 't-text'">{{ k.attacker }}</span><b>击杀</b><span :class="k.victim_team === 'A' ? 'ct-text' : 't-text'">{{ k.victim }}</span>
            <i class="feed-wpn">{{ k.weapon ? k.weapon.replace(/^weapon_/, '') : '' }}</i>
          </template>
        </p>
      </aside>

      <div v-if="currentEvidence" class="evidence-callout">
        <p>关键证据 <b>{{ fmtSec(currentEvidence.time) }}</b></p>
        <span>{{ currentEvidence.title }} · {{ currentEvidence.detail }}</span>
        <em>回到这一秒 · {{ fmtSec(currentEvidence.time) }}</em>
      </div>

      <div class="stage-legend" aria-label="区域控制图例">
        <span><i class="legend-dot t"></i>进攻方控制</span>
        <span><i class="legend-dot ct"></i>防守方控制</span>
        <span><i class="legend-line active"></i>高置信前线</span>
        <span><i class="legend-line low"></i>低置信预测前线</span>
        <span><MorphIcon :icon="X" :size="12" :stroke-width="3" aria-hidden="true" class="legend-death" />死亡位置</span>
        <em v-if="fieldStatus !== 'ready'" class="field-status">{{ fieldStatus === 'loading' ? '正在装载 NavMesh 真值' : 'NavMesh 真值暂不可用' }}</em>
      </div>

      <div class="round-badge" aria-hidden="true">
        <template v-if="roundLabel.winner && currentTime >= duration - 4"><span class="win">胜方 {{ roundLabel.winner }}</span></template>
        <template v-else><span class="rb-ct">CT</span><i>vs</i><span class="rb-t">T</span></template>
      </div>
    </div>

    <div class="player-bar">
      <button class="play-btn" type="button" @click="togglePlay" :aria-label="playing ? '暂停界面预览' : '播放界面预览'">
        <MorphIcon :icon="playing ? Pause : Play" :size="21" :stroke-width="2.2" spring="snappy" reduced-motion="user" />
      </button>
      <button class="step-btn" type="button" @click="prevEvent" aria-label="跳转到上一事件"><MorphIcon :icon="SkipBack" :size="16" reduced-motion="user" /></button>
      <button class="step-btn" type="button" @click="nextEvent" aria-label="跳转到下一事件"><MorphIcon :icon="FastForward" :size="16" reduced-motion="user" /></button>
      <button class="timeline" type="button" @click="seek" aria-label="跳转界面预览时间">
        <span class="timeline-progress" :style="{ width: `${progress * 100}%` }"></span>
        <i
          v-for="(ev, i) in allEventTimes"
          :key="ev"
          class="moment"
          :class="{ on: Math.abs(currentTime - ev) < 0.3 }"
          :style="{ left: `${(ev / duration) * 100}%` }"
        ></i>
      </button>
      <div class="speed-group" role="group" aria-label="播放速度">
        <button v-for="s in SPEEDS" :key="s" type="button" :class="{ on: speed === s }" @click="setSpeed(s)">{{ s }}x</button>
      </div>
      <span class="player-time">{{ clock }} / {{ String(Math.floor(duration / 60)).padStart(2, '0') }}:{{ String(Math.round(duration % 60)).padStart(2, '0') }}</span>
      <button class="event-button" type="button" @click="eventOpen = !eventOpen">
        事件列表 <MorphIcon :icon="ChevronDown" :size="16" reduced-motion="user" />
      </button>
    </div>
    <p class="stage-disclaimer">
      真实回合片段（匿名）· Mirage 雷达底图 · 动效用于展示产品交互，实际结论以你上传的 Demo 解析事件为准
    </p>
  </div>
</template>

<style scoped>
.landing-stage { border: 1px solid var(--border-strong); background: var(--mark-surface-2); box-shadow: var(--mark-shadow); }
.stage-topbar { min-height: 68px; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; padding: 6px 20px; border-bottom: 1px solid var(--border); background: linear-gradient(180deg, #0e141b, #0b0f14); }
.stage-topbar > div { display: flex; gap: 12px; align-items: center; }
.stage-topbar > div:last-child { justify-content: flex-end; }
.team { font-weight: 800; font-size: 12px; letter-spacing: .08em; color: var(--text-secondary); }
.team-badge { width: 26px; height: 26px; border-radius: 6px; display: grid; place-items: center; font-size: 12px; font-weight: 900; letter-spacing: .02em; }
.team-badge.ct { background: var(--info-soft); color: var(--info); border: 1px solid rgba(91,157,217,.35); }
.team-badge.t { background: var(--accent-soft); color: var(--accent-strong); border: 1px solid rgba(230,165,74,.4); }
.round-clock { flex-direction: column; gap: 1px !important; text-align: center; }
.round-clock .clock-num { font-size: 19px; line-height: 1.1; font-variant-numeric: tabular-nums; color: var(--text-primary); }
.round-clock span { color: var(--text-muted); font-size: 9px; }
.round-clock .live-driver { color: var(--text-secondary); font-size: 8px; letter-spacing: .035em; }

.engine { position: relative; aspect-ratio: 1 / 1; overflow: hidden; background: #070a0e; isolation: isolate; contain: layout paint; }
.radar { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; filter: grayscale(.18) brightness(.86) contrast(1.05); opacity: .96; z-index: 0; }
.control-field { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 1; pointer-events: none; contain: strict; }
.grid-lines { position: absolute; inset: 0; background-image: linear-gradient(var(--mark-grid) 1px, transparent 1px), linear-gradient(90deg, var(--mark-grid) 1px, transparent 1px); background-size: 48px 48px; pointer-events: none; z-index: 2; }
.engine::after { content: ''; position: absolute; inset: 0; pointer-events: none; background: radial-gradient(120% 90% at 50% 38%, transparent 46%, rgba(0,0,0,.38) 100%); }
.area-layer { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 3; }

/* 区域骨架：细、淡，仅给地图结构感（不压过控制层/玩家） */
.zone-skel { fill: none; stroke: rgba(160,180,205,.1); stroke-width: .8; stroke-dasharray: 2 5; vector-effect: non-scaling-stroke; }

/* 真值前线来自 scalar field 零等值线；没有按区域描边伪装成 frontier。 */
.control-frontier { fill: none; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.control-frontier.active { stroke: rgba(140,220,255,.98); stroke-width: 2.6; filter: drop-shadow(0 0 3px rgba(91,157,217,.7)); }
.control-frontier.low { stroke: rgba(205,215,228,.7); stroke-width: 1.8; stroke-dasharray: 6 5; }

.kill-line { stroke-width: 1; stroke-opacity: .8; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
.kill-line.ct { stroke: #6fb0ee; }
.kill-line.t { stroke: #f0b35e; }

/* 当前激活区域的真实中文地点标签（质心处，队色，可读） */
.zone-labels { position: absolute; inset: 0; pointer-events: none; z-index: 6; }
.zone-label { position: absolute; transform: translate(-50%,-50%); font-size: 10px; font-weight: 800; letter-spacing: .06em; white-space: nowrap; padding: 1px 6px; border-radius: 4px; background: rgba(8,12,16,.78); text-shadow: 0 1px 1px rgba(0,0,0,.7); }
.zone-label.ct { color: #8fc2f2; border: 1px solid rgba(91,157,217,.5); }
.zone-label.t { color: var(--accent-strong); border: 1px solid rgba(230,165,74,.5); }
.zone-label.contested { color: #cfc0f6; border: 1px solid rgba(176,148,238,.55); }

.death-marker { position: absolute; width: 28px; height: 28px; transform: translate(-50%,-50%); display: grid; place-items: center; color: #f06d65; z-index: 4; filter: drop-shadow(0 1px 2px #05070a) drop-shadow(0 0 5px rgba(229,100,93,.7)); pointer-events: none; }
.death-event-num { position: absolute; right: -3px; bottom: -2px; min-width: 13px; height: 13px; padding: 0 2px; border-radius: 7px; display: grid; place-items: center; background: #111820; border: 1px solid rgba(229,100,93,.8); color: #ffaaa4; font-size: 8px; font-weight: 900; line-height: 1; }

.actor { position: absolute; width: 22px; height: 22px; transform: translate(-50%,-50%); border-radius: 50%; display: grid; place-items: center; font-weight: 900; font-size: 10px; z-index: 5; border: 2px solid #edf1f6; box-shadow: 0 0 0 3px rgba(255,255,255,.14), 0 2px 9px rgba(0,0,0,.7); transition: left 140ms linear, top 140ms linear, opacity var(--dur-mid) var(--ease), transform var(--dur-mid) var(--ease); will-change: left, top, transform, opacity; backface-visibility: hidden; }
.actor.t { background: #e6a54a; color: #17110a; box-shadow: 0 0 0 3px rgba(230,165,74,.22), 0 0 14px rgba(230,165,74,.45), 0 2px 9px rgba(0,0,0,.7); }
.actor.ct { background: #5b9dd9; color: #07111a; box-shadow: 0 0 0 3px rgba(91,157,217,.24), 0 0 14px rgba(91,157,217,.5), 0 2px 9px rgba(0,0,0,.7); }
.actor.dead { opacity: 0; transform: translate(-50%,-50%) scale(.4); }
.actor-id { position: relative; text-shadow: 0 1px 2px rgba(0,0,0,.6); }
.kill-ring { position: absolute; width: 34px; height: 34px; transform: translate(-50%,-50%); border: 2px solid var(--err); border-radius: 50%; z-index: 4; box-shadow: 0 0 0 0 rgba(229,100,93,.4); animation: killPulse var(--dur-slow) var(--ease) forwards; }
@keyframes killPulse { 0% { opacity: 1; transform: translate(-50%,-50%) scale(.4); } 100% { opacity: 0; transform: translate(-50%,-50%) scale(1.4); } }

.kill-feed { position: absolute; right: 14px; top: 14px; width: 236px; padding: 11px 13px; background: rgba(8,12,16,.94); border: 1px solid rgba(73,86,102,.85); z-index: 6; }
.kill-feed p { margin: 0; display: flex; align-items: center; gap: 7px; font-size: 10px; }
.kill-feed p + p { margin-top: 7px; }
.kill-feed .feed-map { color: var(--text-muted); border-bottom: 1px solid var(--border); padding-bottom: 7px; margin-bottom: 8px; }
.kill-feed b { color: var(--text-muted); font-size: 9px; font-weight: 600; }
.ct-text { color: #70ade9; }.t-text { color: var(--accent-strong); }
.feed-wpn { margin-left: auto; color: var(--text-faint); font-size: 9px; font-style: normal; font-variant-numeric: tabular-nums; }

.evidence-callout { position: absolute; left: 16px; bottom: 16px; max-width: 300px; padding: 11px 14px; background: rgba(9,13,18,.95); border: 1px solid var(--border-strong); border-left: 3px solid var(--accent); z-index: 6; }
.evidence-callout p { color: var(--accent); font-size: 11px; font-weight: 700; }
.evidence-callout span { display: block; margin-top: 3px; color: var(--text-secondary); font-size: 11px; }
.evidence-callout em { display: block; margin-top: 7px; color: var(--text-muted); font-size: 9px; font-style: normal; letter-spacing: .03em; }

.stage-legend { position: absolute; right: 14px; bottom: 14px; display: flex; flex-direction: column; gap: 6px; padding: 9px 11px; background: rgba(8,12,16,.94); border: 1px solid var(--border); color: var(--text-secondary); font-size: 9px; z-index: 6; }
.stage-legend span { display: flex; align-items: center; gap: 7px; }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; }
.legend-dot.t { background: var(--accent); }
.legend-dot.ct { background: var(--info); }
.legend-line { width: 14px; border-top: 2px solid rgba(140,220,255,.98); }
.legend-line.low { border-top: 1px dashed rgba(205,215,228,.75); }
.legend-death { color: var(--err); }
.field-status { margin-top: 2px; color: var(--text-muted); font-size: 8px; font-style: normal; }

.round-badge { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); display: flex; align-items: center; gap: 8px; padding: 6px 14px; background: rgba(8,12,16,.86); border: 1px solid var(--border); color: var(--text-muted); font-size: 12px; font-weight: 800; z-index: 6; }
.round-badge span { transition: color var(--dur-mid) var(--ease); }
.round-badge .rb-ct { color: var(--info); }
.round-badge .rb-t { color: var(--accent); }
.round-badge i { color: var(--text-faint); font-size: 9px; font-style: normal; }
.round-badge .win { color: var(--accent-strong); text-shadow: 0 0 12px rgba(240,180,94,.6); }

.player-bar { min-height: 58px; display: flex; align-items: center; gap: 10px; padding: 0 16px; border-top: 1px solid var(--border); background: #0b0f14; }
.play-btn, .step-btn, .event-button { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-primary); min-height: 38px; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; }
.play-btn { width: 42px; }
.step-btn { width: 38px; }
.event-button { gap: 7px; padding: 0 12px; font-size: 11px; }
.timeline { position: relative; flex: 1; height: 18px; border: 0; background: transparent; cursor: pointer; min-width: 0; }
.timeline::before { content: ''; position: absolute; left: 0; right: 0; top: 8px; height: 2px; background: #313a45; }
.timeline-progress { position: absolute; left: 0; top: 8px; height: 2px; background: var(--accent); }
.timeline-progress::after { content: ''; position: absolute; right: -4px; top: -3px; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.moment { position: absolute; top: 4px; width: 2px; height: 10px; background: var(--err); transition: transform var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease); }
.moment.on { transform: scaleY(1.4); box-shadow: 0 0 0 3px rgba(229,100,93,.25); }
.speed-group { display: flex; gap: 3px; }
.speed-group button { min-height: 30px; min-width: 34px; border: 1px solid var(--border); background: var(--bg-elevated); color: var(--text-secondary); font-size: 11px; cursor: pointer; border-radius: var(--radius-sm, 5px); }
.speed-group button.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.player-time { color: var(--text-secondary); font-size: 10px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.stage-disclaimer { margin: 0; padding: 7px 16px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 9px; letter-spacing: .02em; }

@media (max-width: 760px) {
  .stage-topbar { padding: 0 10px; }
  .kill-feed { width: 168px; right: 8px; top: 8px; }
  .kill-feed .feed-row:nth-child(3) { display: none; }
  .evidence-callout { left: 8px; bottom: 8px; max-width: 62%; }
  .stage-legend { display: none; }
  .round-badge { bottom: auto; top: 12px; left: 50%; }
  .actor { width: 20px; height: 20px; }
  .player-bar { gap: 6px; padding: 0 8px; }
  .step-btn, .event-button { display: none; }
  .speed-group { gap: 2px; }
  .speed-group button { min-width: 30px; }
  .stage-disclaimer { line-height: 1.5; }
}
@media (prefers-reduced-motion: reduce) {
  .actor, .control-frontier, .kill-ring, .moment, .death-marker { transition: none; animation: none; }
}
</style>
