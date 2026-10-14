// 6R21F-E5 控制模型 Web Worker：把 computeControlFrame(Dijkstra) 放到主线程外，消除 10Hz 模型 tick 的帧尖峰。
// 6R21F-E6A-BUFFER-POOL：跨 Worker 有界可转移 ArrayBuffer 池（high-water = POOL_HW）。
//   worker 填充 staging pair（cv+mag 两块）→ transfer 给 main；main 复制成自有数组后立刻把同一 pair transfer 回收，
//   池保持流动，绝不隐式无界新分配；池满时合并 superseded 请求（只保留最新 pending），并回告 main 以 cleared inflight。
import { createGraph, addNode, addEdge, areaCentroid, computeControlFrame } from './model.js'
import { buildValues } from './fieldTexture.js'

let g = null, maxAreaId = 0
const cache = new Map() // key(gen|demo|map|cfg|tick|controlsHash) -> frame
let ver = '1.0.0'
let frameCount = 0

// ---- E6A-BUFFER-POOL 池状态 ----
let N = 0                    // cv/mag 元素数 = maxAreaId+1（init 后确定）
const POOL_HW = 6            // high-water：最多同时在途的 staging pair 数（含自由池 + 在 main 的）
const freePairs = []         // 已被 main 回收、可复用的 [cvBuf, magBuf]
let created = 0, reused = 0, returned = 0, occupied = 0, peakOccupied = 0, queueHighWater = 0
let pending = null           // 池满时合并的“最新”请求
const pendingDropped = []    // 被合并（superseded）的 requestId

function poolAlloc() {
  return {
    allocatedBuffers: created,
    reusedBuffers: reused,
    returnedBuffers: returned,
    inUseBuffers: occupied,
    highWaterBuffers: peakOccupied > POOL_HW ? peakOccupied : POOL_HW,
    allocatedBytes: created * 2 * N * 4,
    queueHighWater,
  }
}
function acquirePair() {
  // 优先复用已被 main 回收的空闲 pair；无空闲则按需新分配（不设死硬上限）。
  // 回收型消费者（产品 MapControlReplay）始终有 freePair 复用 → created 停在并发高峰附近（≈high-water），
  // 非回收型消费者（诊断 lab，沿用旧行为）按请求增长；绝不因池空而停滞。
  if (freePairs.length) { const p = freePairs.pop(); reused++; return p }
  created++
  return [new ArrayBuffer(N * 4), new ArrayBuffer(N * 4)]
}
function computeFrame(req) {
  const key = `${req.gen}|${req.demoHash}|${req.mapHash}|${req.cfgHash}|${req.tick}|${req.controlsHash}`
  let f = cache.get(key)
  if (!f) {
    const fr = computeControlFrame(g, req.ctrl || [], req.cfg)
    const v = buildValues(fr, maxAreaId)
    f = { cv: v.cv, mag: v.mag, hash: fr.hash }
    if (cache.size > 96) cache.delete(cache.keys().next().value)
    cache.set(key, f)
  }
  return f
}
function sendFrame(req) {
  const pair = acquirePair()
  if (!pair) return false // 池满：调用方负责合并
  occupied++
  if (occupied > peakOccupied) peakOccupied = occupied
  const t0 = performance.now()
  const f = computeFrame(req)
  const computeMs = performance.now() - t0
  frameCount++
  const cvBuf = pair[0], magBuf = pair[1]
  const cvOut = new Float32Array(cvBuf), magOut = new Float32Array(magBuf)
  cvOut.set(f.cv); magOut.set(f.mag)
  self.postMessage({
    type: 'frame', tick: req.tick, requestedTick: req.tick, resolvedTick: req.tick,
    cv: cvBuf, mag: magBuf, hash: f.hash, maxAreaId, computeMs: +computeMs.toFixed(2),
    frameCount, requestId: req.requestId, gen: req.gen, cfgHash: req.cfgHash,
    demoHash: req.demoHash, mapHash: req.mapHash, controlsHash: req.controlsHash, alloc: poolAlloc(),
  }, [cvBuf, magBuf])
  return true
}

self.onmessage = (e) => {
  const { type } = e.data
  if (type === 'ping') { self.postMessage({ type: 'pong', ver }); return }
  if (type === 'init') {
    const nav = e.data.nav
    g = createGraph(); maxAreaId = 0
    for (const a of nav.areas) { const c = areaCentroid(a); addNode(g, a.id, c.x, c.y); if (a.id > maxAreaId) maxAreaId = a.id }
    for (const a of nav.areas) for (const nb of a.adjacency) if (nb > a.id) addEdge(g, a.id, nb)
    N = maxAreaId + 1
    self.postMessage({ type: 'ready', maxAreaId, ver })
    return
  }
  if (type === 'reset') { cache.clear(); pending = null; pendingDropped.length = 0; self.postMessage({ type: 'resetDone' }); return }
  if (type === 'return') {
    const { buffers } = e.data
    if (Array.isArray(buffers) && buffers.length === 2) { freePairs.push(buffers); returned++; occupied = Math.max(0, occupied - 1) }
    // 池已释放：冲刷合并的 pending
    if (pending) { const p = pending; pending = null; sendFrame(p) }
    return
  }
  if (type === 'frame') {
    const req = e.data
    if (!sendFrame(req)) {
      // 池满：合并 superseded 请求，只保留最新 pending；回告 main 清理 inflight
      if (pending && pending.requestId != null) pendingDropped.push(pending.requestId)
      pending = req
      if (pendingDropped.length) {
        self.postMessage({ type: 'superseded', requestIds: pendingDropped.splice(0) })
        if (pendingDropped.length > queueHighWater) queueHighWater = pendingDropped.length
      }
    }
  }
}
