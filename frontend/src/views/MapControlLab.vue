<script setup>
// 6R21F /map-control-lab（dev-only 内部实验室）：官方 Mirage 2544-area NavMesh + demo19 匿名样本 → 拓扑感知控制场。
// 一切数据来自 dev-only 路由/仓库内样本；不接生产首页。/demo/22(Inferno) 需单独 navmesh（见报告）。
// E5N 高频架构：worker 继续 10Hz 真值端点；主线程缓存"当前两个 exact tick"的端点包
//   { cv,mag,states,scalarGrid,fieldCanvas,fieldChecksum,frontier,controlsHash,hash,cfgHash }
//   仅端点/hash/cfg 变化时构建（10Hz）。每 rAF 只做：cache lookup + frac + 少量 draw（两个端点纹理交叉淡入淡出 + 前线交叉淡入），
//   不再遍历 2544 cv/mag、不再 hashOf 字符串、不 states 计数、不做 per-frame 标量网格重算 → 热路径零全扫描。
// 时间轴：rAF 驱动玩家/控制场/击杀/闪；pause/seek/0.5x/1x/2x/4x/end/reduced-motion 确定性。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { createGraph, addNode, addEdge, areaCentroid, mapPointToArea, computeControlFrame } from '../map-control/model.js'
import { buildAreaGrid, precomputeTexelBlend, computeScalarGrid, renderScalarField, extractContour, stitchContour, buildTexelComp, areaMapOf, buildValues, computeFrontier, precomputeFrontierEdges, smoothScalarCv, MAXN } from '../map-control/fieldTexture.js'
import { computeAreaFrontier } from '../map-control/frontier.js'
import { stateCounts, hashCvMag, diffFrames, frameChecksum, combineFieldChecksum, compareVsSync } from '../map-control/pipeline.js'

const NAV_URL = '/api/maps/de_mirage/navmesh'
const nav = ref(null)
const sample = ref(null)
const err = ref('')
const canvasRef = ref(null)
const radarImg = ref(null)

const currentTime = ref(0)
const playing = ref(false)
const speed = ref(1)
const reduced = ref(false)
const cfg = reactive({ sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200 })
const layer = reactive({ field: true, nav: false, players: true, kills: true, flash: true, labels: false, scalarField: true, front: true })
const SPEEDS = [0.5, 1, 2, 4]

let graph = null
let transform = null
let lastFrame = 0
let rafId = 0
const GRID_N = 128
let areaIdByTexel = null
let probes = [] // 隔墙探测（近屏幕但不同连通分量）

const CDS = { CT: 'rgba(91,157,217,', T: 'rgba(230,165,74,', CONTESTED: 'rgba(226,165,74,' }

onMounted(async () => {
  reduced.value = typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const img = new Image()
  img.src = '/maps/de_mirage.png'
  img.onload = () => { radarImg.value = img; scheduleRender() }
  try {
    const [nres, sres] = await Promise.all([fetch(NAV_URL), fetch('/demo/6r21-round-sample.json')])
    if (!nres.ok) throw new Error('nav json HTTP ' + nres.status + ' (产品路由 /api/maps/de_mirage/navmesh)')
    if (!sres.ok) throw new Error('demo sample HTTP ' + sres.status)
    nav.value = await nres.json()
    sample.value = await sres.json()
    graph = buildGraph(nav.value)
    transform = makeTransform(sample.value.map)
    initField()
    try {
      wStats.boot = performance.now()
      worker = new Worker(new URL('../map-control/model.worker.js', import.meta.url), { type: 'module' })
      worker.onmessage = (e) => { const d = e.data; if (d.type === 'pong') { wStats.ver = d.ver; return; } if (d.type === 'ready') { workerReady = true; wStats.ready = true; wStats.readyMs = +(performance.now() - wStats.boot).toFixed(2); warmWorker(); return; } if (d.type === 'frame') { const k = inflightReqId.get(d.requestId); if (k) { inflight.delete(k); inflightReqId.delete(d.requestId); wStats.queue = Math.max(0, wStats.queue - 1) } if (d.gen !== generation || d.cfgHash !== cfgHashKey() || d.demoHash !== demoHash() || d.mapHash !== mapHash() || d.requestedTick !== d.resolvedTick || d.tick !== d.resolvedTick) { wStats.dropped = (wStats.dropped || 0) + 1; return } const f = { tick: d.tick, gen: d.gen, demoHash: d.demoHash, mapHash: d.mapHash, cfgHash: d.cfgHash, controlsHash: d.controlsHash, requestId: d.requestId, cv: new Float32Array(d.cv), mag: new Float32Array(d.mag), hash: d.hash, source: 'worker', exact: true, requestedTick: d.requestedTick, resolvedTick: d.resolvedTick }; exactFrameCache.set(frameKey(d.tick), f); uniqueTicks.add(d.tick); displayFallback = { cv: f.cv, mag: f.mag, hash: f.hash, exact: false, source: 'nearest-fallback', requestedTick: d.tick, resolvedTick: d.tick }; wStats.frames++; wStats.lastComputeMs = d.computeMs; scheduleRender() } }
      worker.onerror = (e) => { wStats.err = String(e.message || e); workerReady = false }
      worker.onmessageerror = (e) => { wStats.err = 'messageerror ' + (e.message || ''); workerReady = false }
      worker.postMessage({ type: 'init', nav: JSON.parse(JSON.stringify(nav.value)) })
    } catch (e) { worker = null; workerReady = false; wStats.err = String(e) }
    currentTime.value = initialTime()
    scheduleRender()
  } catch (e) {
    err.value = String(e)
  }
})

function makeTransform(map) {
  const { pos_x: px, pos_y: py, scale: s, image_size: n } = map
  return { toRadar: (x, y) => ({ rx: (x - px) / (s * n), ry: (py - y) / (s * n) }), toWorld: (rxx, ryy) => ({ x: px + rxx * s * n, y: py - ryy * s * n }) }
}
function buildGraph(navJson) {
  const g = createGraph()
  for (const a of navJson.areas) { const c = areaCentroid(a); addNode(g, a.id, c.x, c.y) }
  for (const a of navJson.areas) for (const nb of a.adjacency) if (nb > a.id) addEdge(g, a.id, nb)
  return g
}
// 连续控制场：graph-safe blend weights（home + 图邻区，隔墙权重0）+ 端点纹理交叉淡入淡出 + isoline/frontier
let blendIds = null, blendWts = null
let maxAreaId = 0
let frontierEdges = null
let texelComp = null
let frontierAdjacency = null, frontierCentroids = null
let fieldCanvas = null, fieldCtx = null
let compositeFieldCanvas = null, compositeFieldCtx = null
let outCv = new Float32Array(0), outMag = new Float32Array(0)
let worker = null, workerReady = false
const wStats = { boot: 0, ready: false, readyMs: 0, ver: '', err: null, req: 0, frames: 0, sync: 0, queue: 0, lastComputeMs: 0, dropped: 0 }
function warmWorker() {
  const t0t = tickAtTimeSec(currentTime.value)
  for (const dd of [-2, -1, 0, 1, 2]) requestFrame(t0t + dd)
}
function initField() {
  const areaGrid = buildAreaGrid(nav.value, GRID_N, transform.toRadar, transform.toWorld).areaIdByTexel
  areaIdByTexel = areaGrid
  maxAreaId = 0
  for (const a of nav.value.areas) maxAreaId = Math.max(maxAreaId, a.id)
  const adjacencyMap = new Map(nav.value.areas.map((a) => [a.id, new Set(a.adjacency)]))
  const centroidMap = new Map(nav.value.areas.map((a) => { const c = areaCentroid(a); return [a.id, { x: c.x, y: c.y }] }))
  const b = precomputeTexelBlend(nav.value, GRID_N, areaGrid, transform.toWorld, adjacencyMap, centroidMap, 800)
  blendIds = b.ids; blendWts = b.wts
  frontierEdges = precomputeFrontierEdges(nav.value)
  const compOfArea = computeComponents()
  texelComp = buildTexelComp(areaGrid, compOfArea, GRID_N)
  frontierAdjacency = graph.adj
  frontierCentroids = new Map([...graph.nodes.values()].map((n) => [n.id, { x: n.x, y: n.y }]))
  fieldCanvas = document.createElement('canvas'); fieldCanvas.width = GRID_N; fieldCanvas.height = GRID_N
  fieldCtx = fieldCanvas.getContext('2d')
  compositeFieldCanvas = document.createElement('canvas'); compositeFieldCanvas.width = GRID_N; compositeFieldCanvas.height = GRID_N
  compositeFieldCtx = compositeFieldCanvas.getContext('2d')
  probes = computeProbes()
}
// 时间连续：固定模型步长 bracketing（floor/ceil 两个确定性端点交叉淡入淡出；首屏/暂停/seek 立即生成）
const MODEL_H = 0.1
const exactFrameCache = new Map() // frameKey -> worker exact frame
const syncRefCache = new Map() // tick -> sync-reference frame
const endpointCache = new Map() // frameKey -> endpoint package（10Hz 端点；仅 hash/cfg/端点变化时重建）
const inflight = new Set()
const inflightReqId = new Map()
let wStatsLastReq = 0
let displayFallback = null
let generation = 0
let renderEpoch = 0
let lastValues = { cv: new Float32Array(0), mag: new Float32Array(0) }
const alloc = { areaObjectBuilds: 0, fullAreaMapBuilds: 0, legacyFrontierCalls: 0 }
const uniqueTicks = new Set()
let cacheHits = 0
let endpointBuildCount = 0
let hotPathFullScans = 0 // E5N 热路径零全扫描（始终 0；若被打破则 >0，作硬门禁量）
const perfState = { frames: 0, hz: 0, windowStart: 0 }
const phaseMax = { buildMs: 0, compositeMs: 0, drawMs: 0 }
function recordPhaseMax(p) { phaseMax.buildMs = Math.max(phaseMax.buildMs, p.buildMs); phaseMax.compositeMs = Math.max(phaseMax.compositeMs, p.compositeMs); phaseMax.drawMs = Math.max(phaseMax.drawMs, p.drawMs); return maxPhaseName() }
function maxPhaseName() { const m = Math.max(phaseMax.buildMs, phaseMax.compositeMs, phaseMax.drawMs); return m === phaseMax.drawMs ? 'draw' : m === phaseMax.compositeMs ? 'composite' : 'build' }
const tickAtTimeSec = (t) => Math.floor((t + 1e-4) / MODEL_H)
const timeSecAtTick = (tick) => tick * MODEL_H
function cfgObj() { return { sigma: cfg.sigma, neutralThreshold: cfg.neutralThreshold, contestRatio: cfg.contestRatio, maxPropagationDistance: cfg.maxPropagationDistance, frontierBand: 0.05 } }
function cfgHashKey() { const c = cfgObj(); return `${c.sigma}|${c.neutralThreshold}|${c.contestRatio}|${c.maxPropagationDistance}` }
function demoHash() { return sample.value?.source?.sha256 || 'demo19' }
function mapHash() { return nav.value?.source?.sha256 || 'mirage' }
function frameKey(tick) { return `${generation}|${demoHash()}|${mapHash()}|${cfgHashKey()}|${tick}` }
function controlsHash(ctl) { const c = [...ctl].sort((a, b) => (a.areaId - b.areaId) || (a.team < b.team ? -1 : 1)); let h = 0x811c9dc5; for (const p of c) { const s = p.team + ':' + p.areaId; for (let j = 0; j < s.length; j++) { h ^= s.charCodeAt(j); h = (h * 0x01000193) >>> 0 } } return (h >>> 0).toString(16) }
function controlsHashOf(tick) { return controlsHash(aliveControlsAtTick(tick)) }
function resetModelState() { exactFrameCache.clear(); syncRefCache.clear(); inflight.clear(); endpointCache.clear(); displayFallback = null; scheduleRender() }
watch(cfg, () => { generation++; resetModelState() })
watch(layer, () => scheduleRender())
function aliveControlsAtTimeSec(tSec) { return playersAt(tSec).filter((p) => p.alive && p.area != null).map((p) => ({ team: p.side, areaId: p.area, alive: true })) }
function aliveControlsAtTick(tick) { return aliveControlsAtTimeSec(timeSecAtTick(tick)) }
function requestFrame(tick) {
  const key = frameKey(tick)
  if (exactFrameCache.has(key)) { cacheHits++; return exactFrameCache.get(key) }
  if (inflight.has(key)) return null
  if (!worker || !workerReady) return null
  inflight.add(key); wStats.req++; wStats.queue++; const rid = ++wStatsLastReq; inflightReqId.set(rid, key)
  const ctl = aliveControlsAtTick(tick)
  worker.postMessage({ type: 'frame', tick, cfg: cfgObj(), ctrl: ctl, gen: generation, cfgHash: cfgHashKey(), demoHash: demoHash(), mapHash: mapHash(), controlsHash: controlsHash(ctl), requestId: rid })
  return null
}
function resolveFrame(tick) { const e = exactFrameCache.get(frameKey(tick)); if (e) return e; const s = syncRefCache.get(tick); if (s) return s; return displayFallback || null }
function makeSyncReference(tick) {
  const ctl = aliveControlsAtTick(tick)
  const fr = computeControlFrame(graph, ctl, cfgObj())
  const v = buildValues(fr, maxAreaId)
  const f = { tick, gen: generation, demoHash: demoHash(), mapHash: mapHash(), cfgHash: cfgHashKey(), controlsHash: controlsHash(ctl), requestId: 0, cv: v.cv, mag: v.mag, hash: fr.hash, source: 'sync-reference', exact: false, requestedTick: tick, resolvedTick: tick }
  syncRefCache.set(tick, f)
  return f
}
function modelExactNow(tick0, tick1) { const e0 = exactFrameCache.get(frameKey(tick0)), e1 = exactFrameCache.get(frameKey(tick1)); return !!(e0 && e1 && e0.source === 'worker' && e1.source === 'worker' && e0.tick === tick0 && e1.tick === tick1 && e0.gen === generation && e1.gen === generation && e0.cfgHash === cfgHashKey() && e1.cfgHash === cfgHashKey() && e0.demoHash === demoHash() && e1.demoHash === demoHash() && e0.mapHash === mapHash() && e1.mapHash === mapHash()) }

// ---- E5N 端点打包（10Hz；仅在端点/hash/cfg 变化时构建；含全部需要展示/校验的派生数据）----
function fieldCanvasChecksum(fcv) {
  const c = fcv.getContext('2d'); const d = c.getImageData(0, 0, GRID_N, GRID_N).data
  let h = 0x811c9dc5
  for (let i = 0; i < d.length; i += 4) { h ^= (d[i] * 3 + d[i + 1] * 5 + d[i + 2] * 7 + d[i + 3] * 11) >>> 0; h = (h * 0x01000193) >>> 0 }
  return h >>> 0
}
function contourStats(grid) {
  const segs = extractContour(grid.cv, grid.mag, grid.mask, texelComp, GRID_N, 0, cfg.neutralThreshold)
  const linesA = stitchContour(segs.filter((s) => s.active), 3)
  const linesL = stitchContour(segs.filter((s) => !s.active), 3)
  let colored = 0
  for (let i = 0; i < grid.mask.length; i++) if (grid.mask[i] && grid.mag[i] >= cfg.neutralThreshold) colored++
  return { segCount: segs.length, activeLines: linesA.length, predictedLines: linesL.length, activeTotalLen: +linesA.reduce((s, l) => s + polyLenScaled(l), 0).toFixed(1), predictedTotalLen: +linesL.reduce((s, l) => s + polyLenScaled(l), 0).toFixed(1), coloredRatio: +(colored / grid.mask.length).toFixed(4) }
}
function ensureEndpoint(tick) {
  const key = frameKey(tick)
  const f = resolveFrame(tick)
  if (!f) return null
  const cached = endpointCache.get(key)
  if (cached && cached.hash === f.hash && cached.cfgHash === f.cfgHash && cached.gen === generation) return cached
  endpointBuildCount++
  // 标量网格（逐 texel）→ 平滑 → 渲染进独立 128x128 场纹理（breath 用端点 tick 抖动，确定性）
  const grid = computeScalarGrid(blendIds, blendWts, { cv: f.cv, mag: f.mag }, GRID_N)
  const scv = smoothScalarCv(grid.cv, grid.mask, texelComp, GRID_N, 1)
  const fcv = document.createElement('canvas'); fcv.width = GRID_N; fcv.height = GRID_N
  const fctx = fcv.getContext('2d')
  renderScalarField(fctx, GRID_N, { cv: scv, mag: grid.mag, mask: grid.mask }, cfg, tick * 0.6)
  const states = stateCounts(f.cv, f.mag, cfgObj(), maxAreaId)
  const frontier = computeAreaFrontier({ areas: nav.value.areas, cv: f.cv, mag: f.mag, centroids: frontierCentroids, adjacencyMap: frontierAdjacency, band: 0.05, threshold: cfg.neutralThreshold })
  const ck = fieldCanvasChecksum(fcv)
  const probesSnap = probes.map((p) => ({ aId: p.aId, bId: p.bId, dist: p.dist, aState: classifyState(f.cv[p.aId], f.mag[p.aId]), bState: classifyState(f.cv[p.bId], f.mag[p.bId]) }))
  const ep = { tick, hash: f.hash, cfgHash: f.cfgHash, gen: generation, cv: f.cv, mag: f.mag, controlsHash: f.controlsHash, states, scalarGrid: { cv: scv, mag: grid.mag, mask: grid.mask }, fieldCanvas: fcv, fieldChecksum: ck, frontier, probes: probesSnap, contour: contourStats({ cv: scv, mag: grid.mag, mask: grid.mask }) }
  endpointCache.set(key, ep)
  if (endpointCache.size > 8) endpointCache.delete(endpointCache.keys().next().value)
  return ep
}
function compositeField(ep0, ep1, frac) {
  const actx = compositeFieldCtx
  actx.clearRect(0, 0, GRID_N, GRID_N)
  actx.globalAlpha = 1 - frac
  actx.drawImage(ep0.fieldCanvas, 0, 0)
  actx.globalAlpha = frac
  actx.drawImage(ep1.fieldCanvas, 0, 0)
  actx.globalAlpha = 1
  // 可审计连续插值校验和（确定性；bracket 边界共享端点 → 无跳变）
  const fieldChecksum = combineFieldChecksum(ep0.fieldChecksum, ep1.fieldChecksum, frac)
  const f0c = ep0.scalarGrid, f1c = ep1.scalarGrid
  return { fieldChecksum, ep0ck: ep0.fieldChecksum, ep1ck: ep1.fieldChecksum, h0: hashCvMag(ep0.cv, ep0.mag, maxAreaId), h1: hashCvMag(ep1.cv, ep1.mag, maxAreaId) }
}

// ---- 玩家（同时间轴每帧插值；热路径不 mapPointToArea）----
function playerMarkersAt(t) {
  const s = sample.value; if (!s || !graph) return []
  return s.players.map((p) => {
    const pt = posAt(p.points, t)
    const dt = deathTick(p.uid)
    if (!pt || pt.alive === false || (dt != null && t >= dt)) return { uid: p.uid, side: p.side, alive: false }
    const w = transform.toWorld(pt.rx, pt.ry)
    return { uid: p.uid, side: p.side, alive: true, wx: w.x, wy: w.y }
  })
}
function playersAt(t) {
  const s = sample.value; if (!s || !graph) return []
  return s.players.map((p) => {
    const pt = posAt(p.points, t)
    const dt = deathTick(p.uid)
    if (!pt || pt.alive === false || (dt != null && t >= dt)) return { uid: p.uid, side: p.side, alive: false }
    const w = transform.toWorld(pt.rx, pt.ry)
    return { uid: p.uid, side: p.side, alive: true, wx: w.x, wy: w.y, area: mapPointToArea(graph, w.x, w.y) }
  })
}
function aliveCountAt(t) { return playerMarkersAt(t).filter((p) => p.alive).length }

// 测试/证据专用：等目标两端点 exact + 端点 texture/frontier prepared + 目标 t 实际渲染且 __mclab 绑定匹配；stale 时间失败。
async function awaitExactAt(t, timeout = 3000) {
  const tick0 = tickAtTimeSec(t), tick1 = tick0 + 1
  const start = performance.now()
  return new Promise((resolve) => {
    const check = () => {
      requestFrame(tick0); requestFrame(tick1)
      const e0 = exactFrameCache.get(frameKey(tick0)), e1 = exactFrameCache.get(frameKey(tick1))
      const ready = !!(e0 && e1 && e0.source === 'worker' && e1.source === 'worker' && e0.tick === tick0 && e1.tick === tick1 && e0.controlsHash === controlsHashOf(tick0) && e1.controlsHash === controlsHashOf(tick1))
      if (ready) {
        ensureEndpoint(tick0); ensureEndpoint(tick1)
        scheduleRender()
        const wait = () => {
          const ep0 = endpointCache.get(frameKey(tick0)), ep1 = endpointCache.get(frameKey(tick1))
          const endpointsReady = !!(ep0 && ep1 && ep0.fieldCanvas && ep0.frontier && ep1.fieldCanvas && ep1.frontier)
          const m = window.__mclab
          const renderOk = !!(m && m.modelExact === true && JSON.stringify(m.requestedTicks) === JSON.stringify([tick0, tick1]) && JSON.stringify(m.resolvedTicks) === JSON.stringify([tick0, tick1]) && m.frameSources && m.frameSources.every((s) => s === 'worker'))
          if (endpointsReady && renderOk) { resolve({ ok: true, tick0, tick1, hash0: e0.hash, hash1: e1.hash, renderEpoch }); return }
          if (performance.now() - start > timeout) resolve({ ok: false, diagnostic: `endpoints=${endpointsReady} render=${renderOk}` }); else requestAnimationFrame(wait)
        }
        requestAnimationFrame(wait)
      }
      else if (performance.now() - start > timeout) resolve({ ok: false, diagnostic: `e0=${!!e0} e1=${!!e1} inflight=${inflight.size}` }); else requestAnimationFrame(check)
    }
    check()
  })
}
// 独立 parity 快照：把两个 worker exact 端点与纯 sync reference 逐数组比较；只有全真才 comparisonPassed=true。
async function captureExactSnapshot(t, timeout = 3000) {
  const tick0 = tickAtTimeSec(t), tick1 = tick0 + 1
  const start = performance.now()
  return new Promise((resolve) => {
    const check = () => {
      requestFrame(tick0); requestFrame(tick1)
      const e0 = exactFrameCache.get(frameKey(tick0)), e1 = exactFrameCache.get(frameKey(tick1))
      const ready = !!(e0 && e1 && e0.source === 'worker' && e1.source === 'worker' && e0.tick === tick0 && e1.tick === tick1)
      if (ready) {
        const s0 = makeSyncReference(tick0), s1 = makeSyncReference(tick1)
        const wf = [{ cv: e0.cv, mag: e0.mag, hash: e0.hash, controlsHash: e0.controlsHash }, { cv: e1.cv, mag: e1.mag, hash: e1.hash, controlsHash: e1.controlsHash }]
        const sf = [{ cv: s0.cv, mag: s0.mag, hash: s0.hash, controlsHash: s0.controlsHash }, { cv: s1.cv, mag: s1.mag, hash: s1.hash, controlsHash: s1.controlsHash }]
        const r = compareVsSync(wf, sf, cfgObj(), maxAreaId)
        resolve({ ok: r.comparisonPassed, ...r, tick0, tick1 })
        return
      }
      if (performance.now() - start > timeout) resolve({ ok: false, diagnostic: `e0=${!!e0} e1=${!!e1}` }); else requestAnimationFrame(check)
    }
    check()
  })
}
function setMclab(ep0, ep1, frac, alive, comp, tick0, tick1) {
  const e0 = exactFrameCache.get(frameKey(tick0)), e1 = exactFrameCache.get(frameKey(tick1))
  if (typeof window !== 'undefined') window.__mclab = {
    alive, states: ep0 ? ep0.states : null, frameHash: ep0 ? ep0.hash : hashCvMag(lastValues.cv, lastValues.mag, maxAreaId),
    modelExact: modelExactNow(tick0, tick1), requestedTicks: [tick0, tick1], resolvedTicks: [e0 && e0.tick, e1 && e1.tick], frameSources: [e0 && e0.source, e1 && e1.source],
    renderedTicks: [tick0, tick1], hashes: ep0 ? [ep0.hash, ep1 && ep1.hash] : null, frac,
    fieldChecksum: comp.fieldChecksum, ep0ck: comp.ep0ck, ep1ck: comp.ep1ck, scalarHash0: comp.h0, scalarHash1: comp.h1,
    fieldRenderHz: perfState.hz, endpointBuildCount, hotPathFullScans, req: wStats.req, uniqueTicks: uniqueTicks.size, cacheHits,
    gen: generation, cfgHash: cfgHashKey(), demoHash: demoHash(), mapHash: mapHash(), renderEpoch,
    awaitExactAt, captureExactSnapshot, areas: nav.value?.areas?.length || 0, inputSha: nav.value?.source?.sha256 || '', sample: sampleField, stateOf,
    probes: ep0 ? ep0.probes : [], contour: ep0 ? ep0.contour : null, gridN: GRID_N, tick: window.__mclab?.tick || null,
    workerStats: { ready: wStats.ready, readyMs: wStats.readyMs, ver: wStats.ver, err: wStats.err, req: wStats.req, frames: wStats.frames, sync: wStats.sync, queue: wStats.queue, lastComputeMs: wStats.lastComputeMs, inflight: inflight.size, dropped: wStats.dropped || 0 },
    allocCounters: { areaObjectBuilds: alloc.areaObjectBuilds, fullAreaMapBuilds: alloc.fullAreaMapBuilds, legacyFrontierCalls: alloc.legacyFrontierCalls },
  }
}
function computeComponents() {
  const comp = new Map(); let cid = 0
  for (const id of graph.nodes.keys()) {
    if (comp.has(id)) continue
    const stack = [id]; comp.set(id, cid)
    while (stack.length) { const cur = stack.pop(); for (const nb of graph.adj.get(cur) || []) if (!comp.has(nb)) { comp.set(nb, cid); stack.push(nb) } }
    cid++
  }
  return comp
}
function computeProbes() {
  const comp = computeComponents(); const main = largestComp(comp)
  const playerComps = new Set()
  for (const t of [0.2, 0.405, 0.6, 0.85]) { for (const p of playersAt(duration.value * t)) { if (p.alive && p.area != null && comp.has(p.area)) playerComps.add(comp.get(p.area)) } }
  const out = []
  for (const a of graph.nodes.keys()) {
    if (comp.get(a) !== main) continue
    let best = null, bd = Infinity
    for (const b of graph.nodes.keys()) {
      if (comp.get(b) === main) continue
      if (playerComps.has(comp.get(b))) continue
      const A = graph.nodes.get(a), B = graph.nodes.get(b); const d = Math.hypot(A.x - B.x, A.y - B.y)
      if (d < bd) { bd = d; best = b }
    }
    if (best != null && bd < 1300) { out.push({ aId: a, bId: best, dist: Math.floor(bd) }); if (out.length >= 8) break }
  }
  return out
}
function largestComp(comp) {
  const counts = {}
  for (const c of comp.values()) counts[c] = (counts[c] || 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0]
}
function classifyState(cv, mag) { if (mag < cfg.neutralThreshold) return 'NEUTRAL'; if (Math.abs(cv) < cfg.contestRatio) return 'CONTESTED'; return cv > 0 ? 'CT' : 'T' }
function sampleField(wx, wy) {
  if (!areaIdByTexel || !transform || !lastValues.cv.length) return null
  const r = transform.toRadar(wx, wy)
  const tx = Math.min(GRID_N - 1, Math.max(0, Math.floor(r.rx * GRID_N)))
  const ty = Math.min(GRID_N - 1, Math.max(0, Math.floor(r.ry * GRID_N)))
  const id = areaIdByTexel[ty * GRID_N + tx]
  if (id < 0) return { areaId: -1, state: 'WALL', magnitude: 0 }
  if (id > lastValues.cv.length - 1) return { areaId: id, state: 'NONE' }
  return { areaId: id, state: classifyState(lastValues.cv[id], lastValues.mag[id]), magnitude: lastValues.mag[id], controlValue: lastValues.cv[id] }
}
function stateOf(areaId) {
  if (areaId < 0 || areaId > lastValues.cv.length - 1) return { areaId, state: 'NONE' }
  return { areaId, state: classifyState(lastValues.cv[areaId], lastValues.mag[areaId]), magnitude: lastValues.mag[areaId], controlValue: lastValues.cv[areaId] }
}
function posAt(points, t) {
  if (!points || !points.length) return null
  if (t <= points[0].t) return points[0]
  const last = points[points.length - 1]
  if (t >= last.t) return last
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1]
    if (a.t <= t && t <= b.t) { const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0; return { rx: a.rx + (b.rx - a.rx) * f, ry: a.ry + (b.ry - a.ry) * f, alive: b.alive } }
  }
  return points[points.length - 1]
}
const duration = computed(() => sample.value?.round?.max_duration || 0)
const clock = computed(() => { const s = Math.max(0, Math.min(duration.value, currentTime.value)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}` })
function initialTime() {
  const s = sample.value; if (!s) return 0
  const D = s.round.max_duration || 0
  const kills = (s.events?.kills || []).map((k) => Number(k.time)).filter(Number.isFinite)
  function regions(t) { return s.areas.filter((a) => { const seg = (a.segments.A || []).find((x) => t >= x.start && t <= x.end); return seg && seg.state !== 'unknown' }).length }
  let best = 0, bs = -1
  for (let t = 0; t <= D; t += 0.5) { const r = regions(t); const b = kills.filter((k) => t >= k && t <= k + 3).length; const sc = r * 100 + b * 500; if (sc > bs) { bs = sc; best = t } }
  return Math.round(best * 100) / 100
}
function killMarkersAt(t) {
  const s = sample.value; if (!s || !graph) return []
  const out = []
  for (const k of s.events?.kills || []) {
    const kt = Number(k.time); if (!Number.isFinite(kt) || kt > t) continue
    const v = s.players.find((p) => p.uid === k.victim)
    const pt = v && posAt(v.points, kt)
    if (!pt) continue
    const w = transform.toWorld(pt.rx, pt.ry)
    out.push({ time: kt, attacker: k.attacker, victim: k.victim, weapon: (k.weapon || '').replace(/^weapon_/, ''), wx: w.x, wy: w.y })
  }
  return out
}
function flashAt(t) {
  const s = sample.value; if (!s) return []
  return (s.events?.blinds || []).filter((b) => { const st = Number(b.time); return Number.isFinite(st) && Math.abs(t - st) < 0.3 })
}
// ---- 时间轴控制 ----
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
  if (!sample.value) return
  if (currentTime.value >= duration.value) currentTime.value = 0
  if (reduced.value) { playing.value = false; currentTime.value = currentTime.value === 0 ? duration.value : 0; scheduleRender(); return }
  playing.value = !playing.value; lastFrame = 0
  if (playing.value) rafId = requestAnimationFrame(animate)
  else if (rafId) cancelAnimationFrame(rafId)
}
function seek(e) { const v = Number(e.target.value); if (Number.isFinite(v)) { currentTime.value = Math.min(Math.max(0, v), duration.value); scheduleRender() } }
function setSpeed(v) { speed.value = v }
function toEnd() { currentTime.value = duration.value; playing.value = false; scheduleRender() }
let renderQueued = false
function scheduleRender() {
  if (renderQueued) return; renderQueued = true
  requestAnimationFrame(() => { renderQueued = false; render() })
}
// ---- 渲染（Canvas 2D；端点纹理交叉淡入淡出 + 前线交叉淡入；低 alpha 单元场，不画网格边线/不跨墙）----
function render() {
  renderEpoch++
  const cv = canvasRef.value; if (!cv || !graph) return
  const ctx = cv.getContext('2d'); const W = cv.width, H = cv.height
  ctx.clearRect(0, 0, W, H)
  ctx.fillStyle = '#0a0f15'; ctx.fillRect(0, 0, W, H)
  if (radarImg.value) { ctx.globalAlpha = 1; ctx.drawImage(radarImg.value, 0, 0, W, H) }
  const t = currentTime.value
  const toPx = (x, y) => { const r = transform.toRadar(x, y); return [r.rx * W, r.ry * H] }
  const tick0 = tickAtTimeSec(t), tick1 = tick0 + 1
  const frac = tick1 > tick0 ? Math.min(1, Math.max(0, (t - timeSecAtTick(tick0)) / MODEL_H)) : 0
  let ep0 = null, ep1 = null
  if (layer.field && fieldCanvas) {
    const r0 = performance.now()
    requestFrame(tick0); requestFrame(tick1); requestFrame(tick0 + 2); requestFrame(tick0 + 3)
    ep0 = ensureEndpoint(tick0); ep1 = ensureEndpoint(tick1)
    const tBuild = performance.now()
    if (ep0 && ep1) {
      const comp = compositeField(ep0, ep1, frac)
      const tComp = performance.now()
      ctx.imageSmoothingEnabled = true
      ctx.globalAlpha = 1
      if (layer.scalarField) ctx.drawImage(compositeFieldCanvas, 0, 0, W, H)
      if (layer.front) { drawFrontierLines(ctx, W, H, ep0.frontier, 1 - frac); drawFrontierLines(ctx, W, H, ep1.frontier, frac) }
      const active = ep0.frontier ? ep0.frontier.lines.filter((l) => l.active).length : 0
      const low = ep0.frontier ? ep0.frontier.lines.length - active : 0
      cv.setAttribute('data-contour-active-lines', String(active)); cv.setAttribute('data-contour-predicted-lines', String(low)); cv.setAttribute('data-contour-length', String(Math.round(ep0.frontier ? ep0.frontier.lines.reduce((s, l) => s + l.pts.length, 0) : 0)))
      const tDrawDone = performance.now()
      const rMs = Math.round((tDrawDone - r0) * 100) / 100
      cv.setAttribute('data-render-ms', String(rMs))
      const phase = { buildMs: +(tBuild - r0).toFixed(2), compositeMs: +(tComp - tBuild).toFixed(2), drawMs: +(tDrawDone - tComp).toFixed(2), totalMs: +rMs.toFixed(2) }
      recordPhaseMax(phase)
      lastValues = { cv: ep0.cv, mag: ep0.mag }
      perfState.frames++
      const now = performance.now()
      if (!perfState.windowStart) perfState.windowStart = now
      if (now - perfState.windowStart >= 1000) { perfState.hz = Math.round(perfState.frames * 1000 / (now - perfState.windowStart)); perfState.frames = 0; perfState.windowStart = now }
      setMclab(ep0, ep1, frac, aliveCountAt(t), comp, tick0, tick1)
      if (window.__mclab) { window.__mclab.renderMs = rMs; window.__mclab.renderPhase = phase; window.__mclab.maxFramePhase = maxPhaseName() }
    }
  }
  if (layer.nav) {
    ctx.strokeStyle = 'rgba(180,200,220,0.12)'; ctx.lineWidth = 1
    for (const a of nav.value.areas) { ctx.beginPath(); for (let i = 0; i < a.corners.length; i++) { const [px, py] = toPx(a.corners[i][0], a.corners[i][1]); if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py) } ctx.closePath(); ctx.stroke() }
  }
  if (layer.players) {
    for (const p of playerMarkersAt(t)) {
      if (!p.alive) continue
      const [px, py] = toPx(p.wx, p.wy)
      ctx.beginPath(); ctx.arc(px, py, 7, 0, Math.PI * 2)
      ctx.fillStyle = p.side === 'T' ? '#f0a020' : '#5b9dd9'; ctx.fill()
      ctx.lineWidth = 2; ctx.strokeStyle = '#edf1f6'; ctx.stroke()
    }
    for (const p of playerMarkersAt(t)) {
      if (p.alive) continue
      const dt = deathTick(p.uid); if (dt == null || t < dt) continue
      const ptAtDeath = posAt(playerByUid(p.uid)?.points, dt); if (!ptAtDeath) continue
      const w = transform.toWorld(ptAtDeath.rx, ptAtDeath.ry); const [px, py] = toPx(w.x, w.y)
      const fade = Math.max(0.15, 1 - (t - dt) / 4)
      ctx.strokeStyle = `rgba(229,100,93,${fade})`; ctx.lineWidth = 3; ctx.beginPath()
      ctx.moveTo(px - 6, py - 6); ctx.lineTo(px + 6, py + 6); ctx.moveTo(px + 6, py - 6); ctx.lineTo(px - 6, py + 6); ctx.stroke()
    }
  }
  if (layer.kills) {
    for (const k of killMarkersAt(t)) {
      const fade = Math.max(0, 1 - (t - k.time) / 4); if (fade <= 0) continue
      const [px, py] = toPx(k.wx, k.wy)
      ctx.globalAlpha = 0.25 * fade
      ctx.fillStyle = '#e5655d'; ctx.beginPath(); ctx.arc(px, py, 16, 0, Math.PI * 2); ctx.fill()
      ctx.globalAlpha = fade; ctx.fillStyle = '#ffd24a'; ctx.font = 'bold 10px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText(weaponGlyph(k.weapon), px, py); ctx.globalAlpha = 1; ctx.textAlign = 'left'
    }
  }
  if (layer.flash) {
    for (const b of flashAt(t)) {
      const v = playerByUid(b.victim); const pt = v && posAt(v.points, Number(b.time)); if (!pt) continue
      const w = transform.toWorld(pt.rx, pt.ry); const [px, py] = toPx(w.x, w.y)
      ctx.fillStyle = 'rgba(255,255,255,0.4)'; ctx.beginPath(); ctx.arc(px, py, 22, 0, Math.PI * 2); ctx.fill()
    }
  }
}
function drawFrontierLines(ctx, W, H, frontierStats, alphaMul = 1) {
  if (!frontierStats || !transform) return
  const px = (x, y) => { const rr = transform.toRadar(x, y); return [rr.rx * W, rr.ry * H] }
  ctx.save(); ctx.lineCap = 'round'; ctx.lineJoin = 'round'
  for (const line of frontierStats.lines) {
    const [x0, y0] = px(line.pts[0].x, line.pts[0].y)
    ctx.beginPath(); ctx.moveTo(x0, y0)
    for (let i = 1; i < line.pts.length; i++) { const [x, y] = px(line.pts[i].x, line.pts[i].y); ctx.lineTo(x, y) }
    if (line.active) {
      ctx.strokeStyle = 'rgba(0,0,0,0.7)'; ctx.globalAlpha = 0.7 * alphaMul; ctx.lineWidth = 8; ctx.stroke()
      ctx.strokeStyle = 'rgba(91,157,217,0.5)'; ctx.globalAlpha = 0.5 * alphaMul; ctx.lineWidth = 5; ctx.stroke()
      ctx.strokeStyle = 'rgba(230,165,74,0.5)'; ctx.globalAlpha = 0.5 * alphaMul; ctx.lineWidth = 5; ctx.stroke()
      ctx.strokeStyle = 'rgba(120,220,255,0.95)'; ctx.globalAlpha = 0.95 * alphaMul; ctx.lineWidth = 3; ctx.stroke()
    } else {
      ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(140,160,180,0.42)'; ctx.globalAlpha = 0.42 * alphaMul; ctx.lineWidth = 2; ctx.stroke(); ctx.setLineDash([])
    }
  }
  ctx.globalAlpha = 1; ctx.restore()
}
function polyLenScaled(poly) { let s = 0; for (let i = 1; i < poly.length; i++) s += Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]); return s }
function weaponGlyph(w) { return { ak47: 'AK', m4a4: 'M4', usp_silencer: 'USP', fiveseven: '57', mac10: 'MAC', famas: 'FAM', p250: 'P250', galilar: 'GAL', glock_vip: 'GLK' }[w] || (w || '?').slice(0, 3).toUpperCase() }
function playerByUid(uid) { return sample.value?.players?.find((p) => p.uid === uid) }
function deathTick(uid) {
  const p = playerByUid(uid); if (!p) return null
  for (let i = p.points.length - 1; i >= 0; i--) if (!p.points[i].alive) return p.points[i].t
  const s = sample.value
  for (const k of s?.events?.kills || []) if (k.victim === uid && Number.isFinite(Number(k.time))) return Number(k.time)
  return null
}
onBeforeUnmount(() => { if (rafId) cancelAnimationFrame(rafId) })
</script>

<template>
  <div class="mclab">
    <div class="mclab-bar">
      <span class="mclab-title">/map-control-lab · Mirage NavMesh v36 ({{ nav?.areas?.length || '—' }} areas) · demo19 匿名样本</span>
      <label class="mclab-tog"><input type="checkbox" v-model="layer.field" /><span>控制场(总)</span></label>
      <label class="mclab-tog"><input type="checkbox" v-model="layer.scalarField" /><span>连续控制场</span></label>
      <label class="mclab-tog"><input type="checkbox" v-model="layer.front" /><span>控制前线</span></label>
      <label class="mclab-tog"><input type="checkbox" v-model="layer.nav" /><span>NavArea</span></label>
      <label class="mclab-tog"><input type="checkbox" v-model="layer.players" /><span>玩家</span></label>
      <label class="mclab-tog"><input type="checkbox" v-model="layer.kills" /><span>击杀/事件</span></label>
      <label class="mclab-tog"><input type="checkbox" v-model="layer.flash" /><span>闪</span></label>
      <span class="mclab-legend"><i class="l-active"></i>控制前线(实) <i class="l-pred"></i>低置信(虚) <i class="l-kill"></i>击杀(琥珀)</span>
    </div>
    <div class="mclab-controls">
      <button @click="togglePlay" class="mclab-btn">{{ playing ? '暂停' : '播放' }}</button>
      <button @click="toEnd" class="mclab-btn">结束</button>
      <div class="mclab-seg"><button v-for="s in SPEEDS" :key="s" :class="{ on: speed === s }" @click="setSpeed(s)">{{ s }}x</button></div>
      <input class="mclab-tl" type="range" min="0" :max="duration || 1" step="0.01" :value="currentTime" @input="seek" />
      <span class="mclab-time">{{ clock }} / {{ String(Math.floor(duration / 60)).padStart(2, '0') }}:{{ String(Math.round(duration % 60)).padStart(2, '0') }}</span>
      <span v-if="reduced" class="mclab-hint">reduced-motion：静态</span>
    </div>
    <div class="mclab-sliders">
      <label>σ <input type="range" min="300" max="2500" step="10" v-model.number="cfg.sigma" @input="scheduleRender" /></label>
      <label>neutral <input type="range" min="0.02" max="0.5" step="0.01" v-model.number="cfg.neutralThreshold" @input="scheduleRender" /></label>
      <label>contest <input type="range" min="0.02" max="0.6" step="0.01" v-model.number="cfg.contestRatio" @input="scheduleRender" /></label>
      <label>max <input type="range" min="800" max="6000" step="50" v-model.number="cfg.maxPropagationDistance" @input="scheduleRender" /></label>
    </div>
    <div v-if="err" class="mclab-err">{{ err }}</div>
    <canvas ref="canvasRef" class="mclab-canvas" :width="1000" :height="1000" data-testid="mclab-canvas" data-ready="ready"></canvas>
    <p class="mclab-note">低 alpha 单元场沿可走拓扑扩散；neutral 透明；contested 窄前线。demo19/Mirage 同图闭环；Inferno/demo22 需单独 navmesh。</p>
  </div>
</template>

<style scoped>
.mclab { max-width: 1100px; margin: 0 auto; padding: 16px; color: #dbe2ec; }
.mclab-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
.mclab-title { font-weight: 800; font-size: 14px; }
.mclab-legend { margin-left: 6px; display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: #aab4c2; }
.mclab-legend i { display: inline-block; width: 18px; height: 4px; border-radius: 2px; }
.l-active { background: #78dcff; box-shadow: 0 0 0 2px rgba(0,0,0,.5); }
.l-pred { background: transparent; border-top: 2px dashed rgba(140,160,180,.6); }
.l-kill { background: #e5b75e; }
.mclab-tog { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #aab4c2; cursor: pointer; min-height: 44px; min-width: 44px; padding: 8px 10px; }
.mclab-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mclab-btn { border: 1px solid #313a45; background: #0d1218; color: #e7ecf2; padding: 5px 12px; cursor: pointer; min-height: 34px; }
.mclab-seg { display: flex; gap: 3px; }
.mclab-seg button { min-width: 34px; border: 1px solid #313a45; background: #0d1218; color: #aab4c2; cursor: pointer; }
.mclab-seg button.on { color: #f0a020; border-color: #f0a020; }
.mclab-tl { flex: 1; min-width: 120px; accent-color: #f0a020; }
.mclab-time { font-variant-numeric: tabular-nums; font-size: 12px; color: #aab4c2; white-space: nowrap; }
.mclab-hint { color: #f0a020; font-size: 12px; }
.mclab-sliders { display: flex; gap: 14px; flex-wrap: wrap; margin: 8px 0; }
.mclab-sliders label { font-size: 11px; color: #aab4c2; display: inline-flex; align-items: center; gap: 6px; }
.mclab-canvas { width: 100%; max-width: 1000px; border: 1px solid #313a45; border-radius: 8px; background: #080b0f; }
.mclab-err { color: #e5655d; font-size: 13px; margin: 8px 0; }
.mclab-note { color: #7d8c9e; font-size: 11px; margin-top: 8px; }
</style>
