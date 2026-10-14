// 6R21F-E6A 单一真值引擎（浏览器侧）：把 E5N 已验收的 demo19×Mirage NavMesh 连续控制场核心集中到这里，
// 供「主页战术舞台 / demo 产品回放」与「诊断壳实验室」共用，确保只有一套真值逻辑，不复制。
// 职责：navmesh 图 + blend/grid 预计算、worker 真值端点、exact 缓存、端点包构建（标量网格/场纹理/前线/校验和）、
//       连续合成（两端点纹理交叉淡入淡出 + 前线交叉淡入）、awaitExact、captureExactSnapshot parity、热路径指标。
// 纯函数部分（model/fieldTexture/frontier/pipeline）已在共享模块中；本引擎只含浏览器侧编排。
import { createGraph, addNode, addEdge, areaCentroid, mapPointToArea, computeControlFrame } from './model.js'
import { buildAreaGrid, precomputeTexelBlend, computeScalarGrid, renderScalarField, extractContour, stitchContour, buildTexelComp, buildValues, precomputeFrontierEdges, smoothScalarCv } from './fieldTexture.js'
import { computeAreaFrontier } from './frontier.js'
import { stateCounts, diffFrames, frameChecksum, combineFieldChecksum, compareVsSync } from './pipeline.js'

export function createMapControlEngine({ nav, sample, cfg, opts = {}, hooks = {} }) {
  const GRID_N = opts.gridN || 128
  const MODEL_H = opts.modelH || 0.1
  const MAX_EP_CACHE = opts.maxEndpointCache || 6 // current bracket(2) + next(1) + prefetch2(2) + exact waiter 余量

  // ---- 坐标变换（唯一）----
  const mp = sample.map
  const LIM = mp.image_size
  const toRadar = (x, y) => ({ rx: (x - mp.pos_x) / (mp.scale * LIM), ry: (mp.pos_y - y) / (mp.scale * LIM) })
  const toWorld = (rx, ry) => ({ x: mp.pos_x + rx * mp.scale * LIM, y: mp.pos_y - ry * mp.scale * LIM })

  // ---- 图 / 预计算（一次）----
  const g = createGraph()
  let maxAreaId = 0
  for (const a of nav.areas) { const c = areaCentroid(a); addNode(g, a.id, c.x, c.y); if (a.id > maxAreaId) maxAreaId = a.id }
  for (const a of nav.areas) for (const nb of a.adjacency) if (nb > a.id) addEdge(g, a.id, nb)
  const { areaIdByTexel } = buildAreaGrid(nav, GRID_N, toRadar, toWorld)
  const adjacencyMap = new Map(nav.areas.map((a) => [a.id, new Set(a.adjacency)]))
  const centroidMap = new Map(nav.areas.map((a) => { const c = areaCentroid(a); return [a.id, { x: c.x, y: c.y }] }))
  const blend = precomputeTexelBlend(nav, GRID_N, areaIdByTexel, toWorld, adjacencyMap, centroidMap, 800)
  const compOfArea = new Map()
  { let cid = 0; for (const id of g.nodes.keys()) { if (compOfArea.has(id)) continue; const st = [id]; compOfArea.set(id, cid); while (st.length) { const cur = st.pop(); for (const nb of (adjacencyMap.get(cur) || [])) if (!compOfArea.has(nb)) { compOfArea.set(nb, cid); st.push(nb) } } cid++ } }
  const texelComp = buildTexelComp(areaIdByTexel, compOfArea, GRID_N)
  const frontierEdges = precomputeFrontierEdges(nav)
  const frontierAdjacency = g.adj
  const frontierCentroids = new Map([...g.nodes.values()].map((n) => [n.id, { x: n.x, y: n.y }]))

  // ---- 时间轴单位（tick=整数，秒=tick*MODEL_H）----
  const tickAtTimeSec = (t) => Math.floor((t + 1e-4) / MODEL_H)
  const timeSecAtTick = (tick) => tick * MODEL_H
  const cfgObj = () => ({ sigma: cfg.sigma, neutralThreshold: cfg.neutralThreshold, contestRatio: cfg.contestRatio, maxPropagationDistance: cfg.maxPropagationDistance, frontierBand: 0.05 })
  function cfgHashKey() { const c = cfgObj(); return `${c.sigma}|${c.neutralThreshold}|${c.contestRatio}|${c.maxPropagationDistance}` }
  // E6D：neutral 回退（跨地图通用），绝不把 demo/地图身份硬编码为 demo19/mirage。
  function demoHash() { return sample.source?.sha256 || sample.content_hash || 'unknown' }
  function mapHash() { return nav.source?.sha256 || nav.stats?.deterministicHash || 'unknown-map' }

  // ---- 玩家 / 事件（统一时间轴）----
  function posAt(points, t) {
    if (!points || !points.length) return null
    if (t <= points[0].t) return points[0]
    const last = points[points.length - 1]; if (t >= last.t) return last
    for (let i = 0; i < points.length - 1; i++) { const a = points[i], b = points[i + 1]; if (a.t <= t && t <= b.t) { const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0; return { rx: a.rx + (b.rx - a.rx) * f, ry: a.ry + (b.ry - a.ry) * f, alive: b.alive } } }
    return points[points.length - 1]
  }
  function playerByUid(uid) { return sample.players.find((p) => p.uid === uid) }
  // E6D-PERF：死亡时间/位姿在 init 一次预计算（回合固定，不随 t 变），
  // 让 per-frame 的 deathTick 从 O(points)+O(kills)+find 降到 O(1)，消除 playerVisualAt/deathX 的重复扫描（players 块 ~10ms 的主因）。
  // deathVisual 忠实复刻旧解析式 playerVisualAt(dt).find(...)：死者在 dt 处恒为 alive:false（无 wx/wy）→ deathX 不绘制（像素与原一致）。
  const deathTimeByUid = new Map()
  const deathVisualByUid = new Map()
  for (const p of sample.players) {
    let dt = null
    for (let i = p.points.length - 1; i >= 0; i--) if (!p.points[i].alive) { dt = p.points[i].t; break }
    if (dt == null) for (const k of sample.events?.kills || []) if (k.victim === p.uid && Number.isFinite(Number(k.time))) { dt = Number(k.time); break }
    deathTimeByUid.set(p.uid, dt)
    if (dt != null) {
      const pt = posAt(p.points, dt)
      const dead = !pt || pt.alive === false || dt >= dt
      deathVisualByUid.set(p.uid, dead ? { uid: p.uid, side: p.side, alive: false } : null)
    } else {
      deathVisualByUid.set(p.uid, null)
    }
  }
  function deathTick(uid) { return deathTimeByUid.get(uid) ?? null }
  function deathVisual(uid) { return deathVisualByUid.get(uid) || null }
  function playerVisualAt(t) {
    return sample.players.map((p) => {
      const pt = posAt(p.points, t); const dt = deathTick(p.uid)
      if (!pt || pt.alive === false || (dt != null && t >= dt)) return { uid: p.uid, side: p.side, alive: false }
      const w = toWorld(pt.rx, pt.ry)
      return { uid: p.uid, side: p.side, alive: true, wx: w.x, wy: w.y, rx: pt.rx, ry: pt.ry }
    })
  }
  function playersAt(t) {
    return sample.players.map((p) => {
      const pt = posAt(p.points, t); const dt = deathTick(p.uid)
      if (!pt || pt.alive === false || (dt != null && t >= dt)) return { uid: p.uid, side: p.side, alive: false }
      const w = toWorld(pt.rx, pt.ry)
      return { uid: p.uid, side: p.side, alive: true, wx: w.x, wy: w.y, area: mapPointToArea(g, w.x, w.y) }
    })
  }
  function aliveControlsAtTimeSec(tSec) { return playersAt(tSec).filter((p) => p.alive && p.area != null).map((p) => ({ team: p.side, areaId: p.area, alive: true })) }
  function aliveControlsAtTick(tick) { return aliveControlsAtTimeSec(timeSecAtTick(tick)) }
  function aliveCountAt(t) { return playerVisualAt(t).filter((p) => p.alive).length }
  function controlsHash(ctl) { const c = [...ctl].sort((a, b) => (a.areaId - b.areaId) || (a.team < b.team ? -1 : 1)); let h = 0x811c9dc5; for (const p of c) { const s = p.team + ':' + p.areaId; for (let j = 0; j < s.length; j++) { h ^= s.charCodeAt(j); h = (h * 0x01000193) >>> 0 } } return (h >>> 0).toString(16) }
  function controlsHashOf(tick) { return controlsHash(aliveControlsAtTick(tick)) }
  function classifyState(cv, mag) { if (mag < cfg.neutralThreshold) return 'NEUTRAL'; if (Math.abs(cv) < cfg.contestRatio) return 'CONTESTED'; return cv > 0 ? 'CT' : 'T' }

  // ---- worker + exact 缓存 ----
  const exactFrameCache = new Map()
  const MAX_EXACT_CACHE = 48 // 当前 bracket + 预取窗口足用；超过即淘汰，避免驻留 Float32Array 无限增长
  let exactCacheCount = 0
  const syncRefCache = new Map()
  const inflight = new Set()
  const inflightReqId = new Map()
  let wStatsLastReq = 0
  let displayFallback = null
  let generation = 0
  let worker = null, workerReady = false
  const MAX_INFLIGHT = 10 // 有界 inflight 队列：当前 bracket(2)+next+prefetch2+exact waiter 余量；超出则非关键预取降级
  let queueHighWaterMain = 0
  const wStats = { boot: 0, ready: false, readyMs: 0, ver: '', err: null, req: 0, frames: 0, sync: 0, queue: 0, lastComputeMs: 0, dropped: 0 }
  function frameKey(tick) { return `${generation}|${demoHash()}|${mapHash()}|${cfgHashKey()}|${tick}` }
  function resetModelState() { for (const ep of endpointCache.values()) { if (ep.fieldCanvas) { ep.fieldCanvas.width = 0; ep.fieldCanvas.height = 0; closedTextures++ } ep.frontier = null } exactFrameCache.clear(); syncRefCache.clear(); inflight.clear(); inflightReqId.clear(); endpointCache.clear(); displayFallback = null; retainedEndpointCount = 0 }
  function initWorker() {
    wStats.boot = performance.now()
    worker = new Worker(new URL('./model.worker.js', import.meta.url), { type: 'module' })
    worker.onmessage = (e) => { const d = e.data; if (d.type === 'pong') { wStats.ver = d.ver; return; } if (d.type === 'ready') { workerReady = true; wStats.ready = true; wStats.readyMs = +(performance.now() - wStats.boot).toFixed(2); hooks.ready && hooks.ready(); return; } if (d.type === 'superseded') { for (const rid of d.requestIds || []) { const k = inflightReqId.get(rid); if (k) { inflight.delete(k); inflightReqId.delete(rid); wStats.queue = Math.max(0, wStats.queue - 1) } } return; } if (d.type === 'frame') {
      const k = inflightReqId.get(d.requestId); if (k) { inflight.delete(k); inflightReqId.delete(d.requestId); wStats.queue = Math.max(0, wStats.queue - 1) } if (d.gen !== generation || d.cfgHash !== cfgHashKey() || d.demoHash !== demoHash() || d.mapHash !== mapHash() || d.requestedTick !== d.resolvedTick || d.tick !== d.resolvedTick) { recycleBuffers(d.cv, d.mag); wStats.dropped = (wStats.dropped || 0) + 1; return }
      // 吸收 worker 池计数的真实快照（fill/transfer/recycle）
      if (d.alloc) { workerAlloc.allocatedBuffers = d.alloc.allocatedBuffers; workerAlloc.reusedBuffers = d.alloc.reusedBuffers; workerAlloc.returnedBuffers = d.alloc.returnedBuffers; workerAlloc.inUseBuffers = d.alloc.inUseBuffers; workerAlloc.highWaterBuffers = d.alloc.highWaterBuffers; workerAlloc.allocatedBytes = d.alloc.allocatedBytes; workerAlloc.queueHighWater = d.alloc.queueHighWater }
      // 复制为 main 自有数组（不依赖 transferable 缓冲），然后立刻把同一 staging pair 回收给 worker（池保持流动）
      const cv = new Float32Array(d.cv), mag = new Float32Array(d.mag)
      const cvOwn = new Float32Array(cv.length); cvOwn.set(cv)
      const magOwn = new Float32Array(mag.length); magOwn.set(mag)
      recycleBuffers(d.cv, d.mag)
      const f = { tick: d.tick, gen: d.gen, demoHash: d.demoHash, mapHash: d.mapHash, cfgHash: d.cfgHash, controlsHash: d.controlsHash, requestId: d.requestId, cv: cvOwn, mag: magOwn, hash: d.hash, source: 'worker', exact: true, requestedTick: d.requestedTick, resolvedTick: d.resolvedTick }
      exactFrameCache.set(frameKey(d.tick), f); uniqueTicks.add(d.tick); exactCacheCount = exactFrameCache.size; if (exactFrameCache.size > MAX_EXACT_CACHE) { exactFrameCache.delete(exactFrameCache.keys().next().value); exactCacheCount = exactFrameCache.size }
      if (!displayFallback) displayFallback = { cv: null, mag: null, hash: '', exact: false, source: 'nearest-fallback', requestedTick: 0, resolvedTick: 0 }
      displayFallback.cv = cvOwn; displayFallback.mag = magOwn; displayFallback.hash = d.hash; displayFallback.requestedTick = d.tick; displayFallback.resolvedTick = d.tick
      wStats.frames++; wStats.lastComputeMs = d.computeMs; hooks.frame && hooks.frame(); return } }
    worker.onerror = (e) => { wStats.err = String(e.message || e); workerReady = false }
    worker.onmessageerror = (e) => { wStats.err = 'messageerror ' + (e.message || ''); workerReady = false }
    worker.postMessage({ type: 'init', nav: JSON.parse(JSON.stringify(nav)) })
  }
  function recycleBuffers(cvBuf, magBuf) { if (worker) { try { worker.postMessage({ type: 'return', buffers: [cvBuf, magBuf] }, [cvBuf, magBuf]) } catch (err) { wStats.err = 'recycle ' + String(err && err.message || err) } } }
  function requestFrame(tick, low = false) {
    const key = frameKey(tick)
    if (exactFrameCache.has(key)) { cacheHits++; return exactFrameCache.get(key) }
    if (inflight.has(key)) return null
    if (!worker || !workerReady) return null
    if (low && inflight.size >= MAX_INFLIGHT) { if (inflight.size > queueHighWaterMain) queueHighWaterMain = inflight.size; return null }
    inflight.add(key); wStats.req++; wStats.queue++; const rid = ++wStatsLastReq; inflightReqId.set(rid, key)
    if (inflight.size > queueHighWaterMain) queueHighWaterMain = inflight.size
    const ctl = aliveControlsAtTick(tick)
    worker.postMessage({ type: 'frame', tick, cfg: cfgObj(), ctrl: ctl, gen: generation, cfgHash: cfgHashKey(), demoHash: demoHash(), mapHash: mapHash(), controlsHash: controlsHash(ctl), requestId: rid })
    return null
  }
  function resolveFrame(tick) { const e = exactFrameCache.get(frameKey(tick)); if (e) return e; const s = syncRefCache.get(tick); if (s) return s; return displayFallback || null }
  function makeSyncReference(tick) {
    const ctl = aliveControlsAtTick(tick)
    const fr = computeControlFrame(g, ctl, cfgObj())
    const v = buildValues(fr, maxAreaId)
    const f = { tick, gen: generation, demoHash: demoHash(), mapHash: mapHash(), cfgHash: cfgHashKey(), controlsHash: controlsHash(ctl), requestId: 0, cv: v.cv, mag: v.mag, hash: fr.hash, source: 'sync-reference', exact: false, requestedTick: tick, resolvedTick: tick }
    syncRefCache.set(tick, f)
    return f
  }
  function modelExactNow(tick0, tick1) { const e0 = exactFrameCache.get(frameKey(tick0)), e1 = exactFrameCache.get(frameKey(tick1)); return !!(e0 && e1 && e0.source === 'worker' && e1.source === 'worker' && e0.tick === tick0 && e1.tick === tick1 && e0.gen === generation && e1.gen === generation && e0.cfgHash === cfgHashKey() && e1.cfgHash === cfgHashKey() && e0.demoHash === demoHash() && e1.demoHash === demoHash() && e0.mapHash === mapHash() && e1.mapHash === mapHash()) }

  // ---- 端点包装（10Hz；仅端点/hash/cfg 变化时重建；缓存有界=当前 bracket+next+预取2）----
  const endpointCache = new Map()
  let endpointBuildCount = 0
  let hotPathFullScans = 0
  const uniqueTicks = new Set()
  let cacheHits = 0
  // 有界分配/淘汰计数器（暴露于 __mcReplay.allocCounters；用于验证保留内存不单调增长）
  let retainedEndpointCount = 0
  let evictedTextures = 0
  let closedTextures = 0
  let highWaterEndpointCount = 0
  // 跨 Worker 转移池计数：由 worker 每帧回报（fill→transfer→main 复制→main recycle 真实计数）
  const workerAlloc = { allocatedBuffers: 0, reusedBuffers: 0, returnedBuffers: 0, inUseBuffers: 0, highWaterBuffers: 0, allocatedBytes: 0, queueHighWater: 0 }
  // E6A 端点构建复用 scratch（computeScalarGrid/smoothScalarCv 不再每 build 新建 Float32Array → 降低 GC 压力）
  const scratchGrid = { cv: new Float32Array(GRID_N * GRID_N), mag: new Float32Array(GRID_N * GRID_N), mask: new Uint8Array(GRID_N * GRID_N) }
  const scratchSmooth = new Float32Array(GRID_N * GRID_N)
  function fieldCanvasChecksum(grid) {
    // CPU-only：对 scalarGrid cv/mag 量化后按整数逐字节 FNV（避免字符串分配与 canvas getImageData GPU 回读导致帧尖峰）
    let h = 0x811c9dc5
    const n = grid.cv.length
    const m = grid.mag
    for (let i = 0; i < n; i++) {
      let a = Math.round(grid.cv[i] * 1000) >>> 0, b = Math.round(m[i] * 1000) >>> 0
      h ^= a & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (a >>> 8) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (a >>> 16) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (a >>> 24) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= b & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (b >>> 8) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (b >>> 16) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (b >>> 24) & 0xff; h = (h * 0x01000193) >>> 0
    }
    return h >>> 0
  }
  // no-alloc per-area cv/mag 量化 FNV（替代每帧 hashCvMag 的字符串分配；确定性、O(n)、构建期一次性）
  function cvMagHash(cv, mag, maxId = -1) {
    let h = 0x811c9dc5
    const n = Math.min(cv.length, mag.length, maxId < 0 ? cv.length : maxId + 1)
    for (let i = 0; i < n; i++) {
      const a = Math.round(cv[i] * 1000) >>> 0, b = Math.round(mag[i] * 1000) >>> 0
      h ^= a & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (a >>> 8) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (a >>> 16) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (a >>> 24) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= b & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (b >>> 8) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (b >>> 16) & 0xff; h = (h * 0x01000193) >>> 0
      h ^= (b >>> 24) & 0xff; h = (h * 0x01000193) >>> 0
    }
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
  function polyLenScaled(poly) { let s = 0; for (let i = 1; i < poly.length; i++) s += Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]); return s }
  function ensureEndpoint(tick) {
    const key = frameKey(tick)
    const f = resolveFrame(tick)
    if (!f) return null
    const cached = endpointCache.get(key)
    if (cached && cached.hash === f.hash && cached.cfgHash === f.cfgHash && cached.gen === generation) {
      // A seek can reuse an old endpoint while the other endpoint/prefetch is
      // inserted. Refresh recency so that insertion cannot release this bitmap.
      endpointCache.delete(key)
      endpointCache.set(key, cached)
      return cached
    }
    endpointBuildCount++
    const tb0 = performance.now()
    const grid = computeScalarGrid(blend.ids, blend.wts, { cv: f.cv, mag: f.mag }, GRID_N, scratchGrid)
    const tb1 = performance.now()
    const scv = smoothScalarCv(grid.cv, grid.mask, texelComp, GRID_N, 1, scratchSmooth)
    const tb2 = performance.now()
    const fcv = document.createElement('canvas'); fcv.width = GRID_N; fcv.height = GRID_N
    const fctx = fcv.getContext('2d')
    const tb3 = performance.now()
    renderScalarField(fctx, GRID_N, { cv: scv, mag: grid.mag, mask: grid.mask }, cfg, tick * 0.6)
    const tb4 = performance.now()
    const states = stateCounts(f.cv, f.mag, cfgObj(), maxAreaId)
    const frontier = computeAreaFrontier({ areas: nav.areas, cv: f.cv, mag: f.mag, centroids: frontierCentroids, adjacencyMap: frontierAdjacency, band: 0.05, threshold: cfg.neutralThreshold })
    const tb5 = performance.now()
    const ck = fieldCanvasChecksum({ cv: scv, mag: grid.mag })
    const tb6 = performance.now()
    // 分阶段累积最大耗时（定位 33ms 长帧来源；不进 per-frame 热路径外传）
    const ph = { grid: +(tb1 - tb0).toFixed(2), smooth: +(tb2 - tb1).toFixed(2), canvasCtx: +(tb3 - tb2).toFixed(2), render: +(tb4 - tb3).toFixed(2), frontier: +(tb5 - tb4).toFixed(2), checksum: +(tb6 - tb5).toFixed(2), total: +(tb6 - tb0).toFixed(2) }
    buildPhaseMax.grid = Math.max(buildPhaseMax.grid, ph.grid); buildPhaseMax.smooth = Math.max(buildPhaseMax.smooth, ph.smooth); buildPhaseMax.canvasCtx = Math.max(buildPhaseMax.canvasCtx, ph.canvasCtx); buildPhaseMax.render = Math.max(buildPhaseMax.render, ph.render); buildPhaseMax.frontier = Math.max(buildPhaseMax.frontier, ph.frontier); buildPhaseMax.checksum = Math.max(buildPhaseMax.checksum, ph.checksum); buildPhaseMax.total = Math.max(buildPhaseMax.total, ph.total)
    // 端点只保留渲染所需（fieldCanvas+frontier）+ 元数据 + 预计算 hash；不持有 full grid cv/mag（降低分配/GC，且让 transfer buffer 可即时回收）
    const cvHash = cvMagHash(f.cv, f.mag, maxAreaId)
    const ep = { tick, hash: f.hash, cfgHash: f.cfgHash, gen: generation, controlsHash: f.controlsHash, states, fieldCanvas: fcv, fieldChecksum: ck, cvHash, frontier, contour: null }
    endpointCache.delete(key)
    endpointCache.set(key, ep)
    retainedEndpointCount = endpointCache.size
    if (endpointCache.size > highWaterEndpointCount) highWaterEndpointCount = endpointCache.size
    if (endpointCache.size > MAX_EP_CACHE) {
      const evict = endpointCache.keys().next().value // LRU: preserve just-used bracket endpoints
      const old = endpointCache.get(evict)
      endpointCache.delete(evict)
      if (old && old.fieldCanvas) { old.fieldCanvas.width = 0; old.fieldCanvas.height = 0; closedTextures++ } // 显式释放 canvas 位图
      if (old) old.frontier = null
      evictedTextures++
      retainedEndpointCount = endpointCache.size
    }
    return ep
  }
  // 提前构建下一 bracket 端点：在 leadTicks 里选第一个「exact 已到且端点未构建」的，每帧至多构建一个，
  // 把 10Hz 边界构建摊到普通帧，避免边界帧重构建+主绘制超预算（minFps 回稳）。
  function prebuildAhead(leadTicks) {
    for (const lt of leadTicks) {
      const key = frameKey(lt)
      if (endpointCache.has(key)) continue
      const ef = exactFrameCache.get(key)
      if (!ef) continue
      if (ensureEndpoint(lt)) return lt
    }
    return null
  }
  function compositeField(ep0, ep1, frac, compositeCanvas) {
    const actx = compositeCanvas.getContext('2d')
    actx.clearRect(0, 0, GRID_N, GRID_N)
    actx.globalAlpha = 1 - frac
    actx.drawImage(ep0.fieldCanvas, 0, 0)
    actx.globalAlpha = frac
    actx.drawImage(ep1.fieldCanvas, 0, 0)
    actx.globalAlpha = 1
    return { fieldChecksum: combineFieldChecksum(ep0.fieldChecksum, ep1.fieldChecksum, frac), ep0ck: ep0.fieldChecksum, ep1ck: ep1.fieldChecksum, h0: ep0.cvHash, h1: ep1.cvHash }
  }
  function bracket(t) { const tick0 = tickAtTimeSec(t), tick1 = tick0 + 1; return { tick0, tick1, frac: tick1 > tick0 ? Math.min(1, Math.max(0, (t - timeSecAtTick(tick0)) / MODEL_H)) : 0 } }
  function ensureBracket(t) { const { tick0, tick1, frac } = bracket(t); requestFrame(tick0); requestFrame(tick1); requestFrame(tick0 + 2, true); requestFrame(tick0 + 3, true); const ep0 = ensureEndpoint(tick0), ep1 = ensureEndpoint(tick1); return { tick0, tick1, frac, ep0, ep1 } }

  // ---- 测试/证据 hooks ----
  function awaitExactAt(t, timeout = 3000) {
    const { tick0, tick1 } = bracket(t)
    const start = performance.now()
    return new Promise((resolve) => {
      const check = () => {
        requestFrame(tick0); requestFrame(tick1)
        const e0 = exactFrameCache.get(frameKey(tick0)), e1 = exactFrameCache.get(frameKey(tick1))
        const ready = !!(e0 && e1 && e0.source === 'worker' && e1.source === 'worker' && e0.tick === tick0 && e1.tick === tick1 && e0.controlsHash === controlsHashOf(tick0) && e1.controlsHash === controlsHashOf(tick1))
        if (ready) { ensureEndpoint(tick0); ensureEndpoint(tick1); hooks.render && hooks.render(); const wait = () => { const ep0 = endpointCache.get(frameKey(tick0)), ep1 = endpointCache.get(frameKey(tick1)); const endpointsReady = !!(ep0 && ep1 && ep0.fieldCanvas && ep0.frontier && ep1.fieldCanvas && ep1.frontier); const ok = hooks.awaitReady ? hooks.awaitReady(tick0, tick1) : true; if (endpointsReady && ok) { resolve({ ok: true, tick0, tick1, hash0: e0.hash, hash1: e1.hash }); return } if (performance.now() - start > timeout) resolve({ ok: false, diagnostic: `endpoints=${endpointsReady} contract=${ok}` }); else requestAnimationFrame(wait) }; requestAnimationFrame(wait); return }
        if (performance.now() - start > timeout) resolve({ ok: false, diagnostic: `e0=${!!e0} e1=${!!e1} inflight=${inflight.size}` }); else requestAnimationFrame(check)
      }
      check()
    })
  }
  function captureExactSnapshot(t, timeout = 3000) {
    const { tick0, tick1 } = bracket(t)
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
          resolve({ ok: r.comparisonPassed, ...r, tick0, tick1 }); return
        }
        if (performance.now() - start > timeout) resolve({ ok: false, diagnostic: `e0=${!!e0} e1=${!!e1}` }); else requestAnimationFrame(check)
      }
      check()
    })
  }

  // ---- 指标 ----
  const perfState = { frames: 0, hz: 0, windowStart: 0 }
  const phaseMax = { buildMs: 0, compositeMs: 0, drawMs: 0 }
  const buildPhaseMax = { grid: 0, smooth: 0, canvasCtx: 0, render: 0, frontier: 0, checksum: 0, total: 0 }
  function tickFieldRender() {
    perfState.frames++
    const now = performance.now()
    if (!perfState.windowStart) perfState.windowStart = now
    if (now - perfState.windowStart >= 1000) { perfState.hz = Math.round(perfState.frames * 1000 / (now - perfState.windowStart)); perfState.frames = 0; perfState.windowStart = now }
  }
  function recordPhase(phase) { phaseMax.buildMs = Math.max(phaseMax.buildMs, phase.buildMs); phaseMax.compositeMs = Math.max(phaseMax.compositeMs, phase.compositeMs); phaseMax.drawMs = Math.max(phaseMax.drawMs, phase.drawMs) }
  function maxPhaseName() { const m = Math.max(phaseMax.buildMs, phaseMax.compositeMs, phaseMax.drawMs); return m === phaseMax.drawMs ? 'draw' : m === phaseMax.compositeMs ? 'composite' : 'build' }
  // E6A-BUFFER-POOL：telemetry 复用单一对象（取消每帧新建大对象 → GC 尖峰）；嵌套子对象原地改写，绝不每帧重建
  const telemetry = { fieldRenderHz: 0, endpointBuildCount: 0, hotPathFullScans: 0, req: 0, uniqueTicks: 0, cacheHits: 0, maxFramePhase: '', phaseMax, buildPhaseMax, workerReady: false, workerStats: {}, allocCounters: {} }
  telemetry.workerStats = { ready: false, readyMs: 0, ver: '', err: null, req: 0, frames: 0, sync: 0, queue: 0, lastComputeMs: 0, inflight: 0, dropped: 0 }
  telemetry.allocCounters = { allocatedBuffers: 0, reusedBuffers: 0, returnedBuffers: 0, inUseBuffers: 0, highWaterBuffers: 0, allocatedBytes: 0, retainedEndpointCount: 0, evicted: 0, closedTextures: 0, queueHighWater: 0, highWaterEndpointCount: 0, exactCacheCount: 0 }
  function metrics() {
    telemetry.fieldRenderHz = perfState.hz
    telemetry.endpointBuildCount = endpointBuildCount
    telemetry.hotPathFullScans = hotPathFullScans
    telemetry.req = wStats.req
    telemetry.uniqueTicks = uniqueTicks.size
    telemetry.cacheHits = cacheHits
    telemetry.maxFramePhase = maxPhaseName()
    const ws = telemetry.workerStats
    ws.ready = wStats.ready; ws.readyMs = wStats.readyMs; ws.ver = wStats.ver; ws.err = wStats.err; ws.req = wStats.req; ws.frames = wStats.frames; ws.sync = wStats.sync; ws.queue = wStats.queue; ws.lastComputeMs = wStats.lastComputeMs; ws.inflight = inflight.size; ws.dropped = wStats.dropped || 0
    const ac = telemetry.allocCounters
    ac.allocatedBuffers = workerAlloc.allocatedBuffers; ac.reusedBuffers = workerAlloc.reusedBuffers; ac.returnedBuffers = workerAlloc.returnedBuffers; ac.inUseBuffers = workerAlloc.inUseBuffers; ac.highWaterBuffers = workerAlloc.highWaterBuffers; ac.allocatedBytes = workerAlloc.allocatedBytes; ac.retainedEndpointCount = retainedEndpointCount; ac.evicted = evictedTextures; ac.closedTextures = closedTextures; ac.queueHighWater = Math.max(queueHighWaterMain, workerAlloc.queueHighWater); ac.highWaterEndpointCount = highWaterEndpointCount; ac.exactCacheCount = exactCacheCount
    return telemetry
  }

  function dispose() {
    if (worker) worker.terminate(); worker = null; workerReady = false
    exactFrameCache.clear(); syncRefCache.clear(); inflight.clear(); inflightReqId.clear(); endpointCache.clear(); uniqueTicks.clear(); displayFallback = null
    retainedEndpointCount = 0; evictedTextures = 0; closedTextures = 0; highWaterEndpointCount = 0; queueHighWaterMain = 0
    workerAlloc.allocatedBuffers = 0; workerAlloc.reusedBuffers = 0; workerAlloc.returnedBuffers = 0; workerAlloc.inUseBuffers = 0; workerAlloc.highWaterBuffers = 0; workerAlloc.allocatedBytes = 0; workerAlloc.queueHighWater = 0
  }
  return {
    initWorker, dispose, requestFrame, ensureEndpoint, ensureBracket, compositeField, bracket,
    awaitExactAt, captureExactSnapshot, aliveCountAt, playerVisualAt, playersAt, aliveControlsAtTick,
    deathTick, deathVisual, playerByUid, modelExactNow, metrics, tickFieldRender, recordPhase, maxPhaseName,
    prebuildAhead,
    get maxAreaId() { return maxAreaId }, get workerReady() { return workerReady }, get gridN() { return GRID_N },
    get exactFrameCache() { return exactFrameCache }, get endpointCache() { return endpointCache },
    get toRadar() { return toRadar }, get toWorld() { return toWorld }, cfgObj, classifyState,
    touchGeneration() { generation++; resetModelState() },
  }
}
