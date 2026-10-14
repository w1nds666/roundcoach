// 6R21F-E2 统一管线：离线扫描 与 /map-control-lab 共用**同一**数据管线，消除重复实现导致的 parity 真值不一致。
// 不在测试里伪造结果、不降低 active 阈值、不把 low-confidence 改名为 active。
import { createGraph, addNode, addEdge, areaCentroid, mapPointToArea, computeControlFrame } from './model.js'
import { buildAreaGrid, precomputeTexelBlend, computeScalarGrid, buildValues, buildTexelComp, extractContour, stitchContour, smoothScalarCv } from './fieldTexture.js'

export function createPipeline({ nav, sample, cfg, opts = {} }) {
  const gridN = opts.gridN || 128
  const sigmaBlend = opts.sigmaBlend ?? 800
  const modelH = opts.modelH ?? 0.1
  const cacheLimit = opts.cacheLimit ?? 48
  const mp = sample.map
  const LIM = mp.image_size
  // 击杀事件是生死状态的权威时钟。部分 Demo 轨迹会在死亡前一帧结束，
  // 因而最后一个 position sample 仍标记 alive=true；若只信轨迹末帧，
  // 死亡玩家会永久残留在控制输入里。这里以最早 victim event 为硬截止。
  const deathByUid = new Map()
  for (const kill of sample.events?.kills || []) {
    const t = Number(kill.time)
    if (!kill.victim || !Number.isFinite(t)) continue
    const previous = deathByUid.get(kill.victim)
    if (previous == null || t < previous) deathByUid.set(kill.victim, t)
  }
  const deathTimes = [...new Set(deathByUid.values())].sort((a, b) => a - b)
  const toRadar = (x, y) => ({ rx: (x - mp.pos_x) / (mp.scale * LIM), ry: (mp.pos_y - y) / (mp.scale * LIM) })
  const toWorld = (rx, ry) => ({ x: mp.pos_x + rx * mp.scale * LIM, y: mp.pos_y - ry * mp.scale * LIM })

  const g = createGraph()
  let maxAreaId = 0
  for (const a of nav.areas) { const c = areaCentroid(a); addNode(g, a.id, c.x, c.y); if (a.id > maxAreaId) maxAreaId = a.id }
  for (const a of nav.areas) for (const nb of a.adjacency) if (nb > a.id) addEdge(g, a.id, nb)
  const adjacencyMap = new Map(nav.areas.map((a) => [a.id, new Set(a.adjacency)]))
  const centroidMap = new Map(nav.areas.map((a) => { const c = areaCentroid(a); return [a.id, { x: c.x, y: c.y }] }))
  const { areaIdByTexel } = buildAreaGrid(nav, gridN, toRadar, toWorld)
  const blend = precomputeTexelBlend(nav, gridN, areaIdByTexel, toWorld, adjacencyMap, centroidMap, sigmaBlend)
  const compOfArea = new Map()
  { let cid = 0; for (const id of g.nodes.keys()) { if (compOfArea.has(id)) continue; const st = [id]; compOfArea.set(id, cid); while (st.length) { const cur = st.pop(); for (const nb of (adjacencyMap.get(cur) || [])) if (!compOfArea.has(nb)) { compOfArea.set(nb, cid); st.push(nb) } } cid++ } }
  const texelComp = buildTexelComp(areaIdByTexel, compOfArea, gridN)

  function posAt(points, t) {
    if (!points || !points.length) return null
    if (t <= points[0].t) return points[0]
    const last = points[points.length - 1]; if (t >= last.t) return last
    for (let i = 0; i < points.length - 1; i++) { const a = points[i], b = points[i + 1]; if (a.t <= t && t <= b.t) { const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0; return { rx: a.rx + (b.rx - a.rx) * f, ry: a.ry + (b.ry - a.ry) * f, alive: t >= b.t ? b.alive : a.alive } } }
    return points[points.length - 1]
  }
  function playersAt(t) {
    return sample.players.map((p) => {
      const pt = posAt(p.points, t)
      const deathAt = deathByUid.get(p.uid)
      if (!pt || pt.alive === false || (deathAt != null && t >= deathAt)) {
        return { uid: p.uid, side: p.side, alive: false, area: -1, deathAt: deathAt ?? null }
      }
      const w = toWorld(pt.rx, pt.ry)
      return { uid: p.uid, side: p.side, alive: true, wx: w.x, wy: w.y, area: mapPointToArea(g, w.x, w.y), deathAt: deathAt ?? null }
    })
  }
  function aliveControls(t) { return playersAt(t).filter((p) => p.alive && p.area != null).map((p) => ({ team: p.side, areaId: p.area, alive: true })) }

  const cache = new Map()
  const cvBuf = new Float32Array(maxAreaId + 1)
  const magBuf = new Float32Array(maxAreaId + 1)
  function snapshot(st) {
    let f = cache.get(st)
    if (!f) {
      const fr = computeControlFrame(g, aliveControls(st), cfg)
      const v = buildValues(fr, maxAreaId)
      f = { cv: v.cv, mag: v.mag, fr }
      cache.set(st, f)
      if (cache.size > cacheLimit) cache.delete(cache.keys().next().value)
    }
    return f
  }
  function frameAt(t) {
    const st0 = Math.floor(t / modelH) * modelH
    const st1 = st0 + modelH
    // 生死状态是离散事件，不能在死亡点两侧做普通插值：否则会在事件前
    // “预先减弱”或在事件后短暂保留死者影响。把当前 0.1s 模型区间按死亡点切段。
    let leftT = st0
    let rightT = st1
    for (const deathAt of deathTimes) {
      if (deathAt <= st0 || deathAt > st1) continue
      if (t >= deathAt) leftT = Math.max(leftT, deathAt)
      else { rightT = Math.min(rightT, deathAt - 1e-6); break }
    }
    const f0 = snapshot(leftT), f1 = snapshot(rightT)
    const span = rightT - leftT
    const frac = span > 0 ? Math.min(1, Math.max(0, (t - leftT) / span)) : 0
    for (let i = 0; i <= maxAreaId; i++) { cvBuf[i] = f0.cv[i] + (f1.cv[i] - f0.cv[i]) * frac; magBuf[i] = f0.mag[i] + (f1.mag[i] - f0.mag[i]) * frac }
    return { cv: cvBuf, mag: magBuf, frac }
  }
  // 确定性 hash（parity 用；对 per-area cv/mag 量化后 FNV）
  function frameHash(t) {
    const v = frameAt(t); let h = 0x811c9dc5
    for (let i = 0; i <= maxAreaId; i++) {
      const s = Math.round(v.cv[i] * 1000).toString() + ':' + Math.round(v.mag[i] * 1000).toString()
      for (let j = 0; j < s.length; j++) { h ^= s.charCodeAt(j); h = (h * 0x01000193) >>> 0 }
    }
    return h >>> 0
  }
  function scalarGridAt(t) { const v = frameAt(t); return computeScalarGrid(blend.ids, blend.wts, v, gridN) }
  function contourAt(t) {
    const grid = scalarGridAt(t)
    // masked 平滑 cv（同可走+同分量，不跨墙/洞）→ 零等值线连续可读（非全局 blur）
    const scv = smoothScalarCv(grid.cv, grid.mask, texelComp, gridN, 2)
    const segs = extractContour(scv, grid.mag, grid.mask, texelComp, gridN, 0, cfg.neutralThreshold)
    const active = segs.filter((s) => s.active), low = segs.filter((s) => !s.active)
    return { grid, scv, segs, active, low, activeLines: stitchContour(active, 0.5), lowLines: stitchContour(low, 2), activeCount: active.length, lowCount: low.length }
  }
  function invalidate() { cache.clear() }
  return { toRadar, toWorld, gridN, maxAreaId, g, blend, texelComp, playersAt, aliveControls, frameAt, frameHash, scalarGridAt, contourAt, invalidate, stateCounts, hashCvMag, diffFrames, frameChecksum, combineFieldChecksum }
}

// ---- E5N 纯函数（node 可测；lab/evidence 共用；O(n) 只在端点构建/显式快照时调用，绝不进 rAF 热路径）----

// 单遍分类 state counts（cv > contest band 定 CT/T；|cv| < contestRatio 为 CONTESTED；mag < neutralThreshold 为 NEUTRAL）
export function stateCounts(cv, mag, cfg, maxId = -1) {
  const counts = { CT: 0, T: 0, CONTESTED: 0, NEUTRAL: 0 }
  const n = Math.min(cv.length, mag.length, maxId < 0 ? cv.length : maxId + 1)
  for (let id = 0; id < n; id++) {
    const m = mag[id]
    if (m < cfg.neutralThreshold) counts.NEUTRAL++
    else if (Math.abs(cv[id]) < cfg.contestRatio) counts.CONTESTED++
    else if (cv[id] > 0) counts.CT++
    else counts.T++
  }
  return counts
}

// per-area cv/mag 量化后 FNV-1a（确定性；与 pipeline.frameHash 同款，但纯数组输入）
export function hashCvMag(cv, mag, maxId = -1) {
  let h = 0x811c9dc5
  const n = Math.min(cv.length, mag.length, maxId < 0 ? cv.length : maxId + 1)
  for (let i = 0; i < n; i++) {
    const s = Math.round(cv[i] * 1000) + ':' + Math.round(mag[i] * 1000)
    for (let j = 0; j < s.length; j++) { h ^= s.charCodeAt(j); h = (h * 0x01000193) >>> 0 }
  }
  return h >>> 0
}

// 逐数组比较两个帧（cv 与 mag 逐元素），返回 diffCount（任一分量差 >1e-9 即计）+ maxAbsDiff（cv/mag 最大值差）
export function diffFrames(a, b) {
  const n = Math.min(a.cv.length, b.cv.length, a.mag.length, b.mag.length)
  let diffCount = 0, maxAbsDiff = 0
  for (let i = 0; i < n; i++) {
    const dc = Math.abs(a.cv[i] - b.cv[i]), dm = Math.abs(a.mag[i] - b.mag[i])
    if (dc > 1e-9 || dm > 1e-9) diffCount++
    if (dc > maxAbsDiff) maxAbsDiff = dc
    if (dm > maxAbsDiff) maxAbsDiff = dm
  }
  return { diffCount, maxAbsDiff, length: n }
}

// 帧级校验和（连续性用；对 per-area cv/mag 量化后累加取模，O(n)）
export function frameChecksum(cv, mag, maxId = -1) {
  let s = 0
  const n = Math.min(cv.length, mag.length, maxId < 0 ? cv.length : maxId + 1)
  for (let i = 0; i < n; i++) { s = (s + Math.round(cv[i] * 1e6) + Math.round(mag[i] * 1e6)) % 2147483647 }
  return s >>> 0
}

// 连续性：两端点 fieldChecksum 随 frac 的连续、可审计插值（确定性；相邻帧差有界；bracket 边界共享端点→无跳变）
export function combineFieldChecksum(ck0, ck1, frac) {
  const f = Math.min(1, Math.max(0, frac))
  return Math.round((ck0 + f * (ck1 - ck0)) * 1000)
}

// captureExactSnapshot 的纯 parity 报告：worker exact 帧 vs 纯 sync reference 帧 逐数组比较。
// workerFrames/syncFrames 各为 [帧0, 帧1]，帧 = { cv, mag, hash, controlsHash }。
// 只有 diffCount===0 且 hash/counts/controlsHash 全匹配才 comparisonPassed=true。
export function compareVsSync(workerFrames, syncFrames, cfg, maxId = -1) {
  const n = Math.min(workerFrames.length, syncFrames.length)
  if (n === 0) return { diffCount: 0, maxAbsDiff: 0, length: 0, hashMatch: false, countsMatch: false, controlsHashMatch: false, comparisonPassed: false }
  let diffCount = 0, maxAbsDiff = 0, length = 0
  let hash = true, ct = true, ctrl = true
  for (let i = 0; i < n; i++) {
    const w = workerFrames[i], s = syncFrames[i]
    const d = diffFrames(w, s)
    diffCount += d.diffCount
    if (d.maxAbsDiff > maxAbsDiff) maxAbsDiff = d.maxAbsDiff
    length = d.length
    if (w.hash !== s.hash) hash = false
    const wc = stateCounts(w.cv, w.mag, cfg, maxId), sc = stateCounts(s.cv, s.mag, cfg, maxId)
    if (wc.CT !== sc.CT || wc.T !== sc.T || wc.CONTESTED !== sc.CONTESTED || wc.NEUTRAL !== sc.NEUTRAL) ct = false
    if (w.controlsHash !== s.controlsHash) ctrl = false
  }
  const comparisonPassed = diffCount === 0 && hash && ct && ctrl
  return { diffCount, maxAbsDiff, length, hashMatch: hash, countsMatch: ct, controlsHashMatch: ctrl, comparisonPassed }
}
