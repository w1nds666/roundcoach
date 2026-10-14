// 6R21F Milestone D2 连续控制场渲染器（纯渲染，不改 model）。
// 真连续：每个可走 texel 用**graph-safe blend weights** 插值——含所在 area 与**仅真实可走邻接边**的邻区，
// 按到质心距离的稳定高斯权重混合 controlValue/magnitude；墙/非邻接边权重严格 0 → 隔墙硬断、无跨墙采样。
// 主视觉 = 真实零交叉 isoline/frontier：对图相邻且 controlValue 异号/跨 contest band 的共享边提取窄前线。
// 禁：逐-area 填色、凸包、区域圈线、全图二分、全局 blur、跨墙热图。
// 纯函数部分（precomputeTexelBlend / blendScalar / computeFrontier / texelScalar）可 node 单测。

export const MAXN = 7 // 每 texel 候选（home + 最多 6 邻区）

export function buildAreaGrid(nav, gridN, toRadar, toWorld) {
  // 预计算：texel -> 所在 areaId（静态，一次）。wall/outside -> -1（透明硬屏障）
  const areaIdByTexel = new Int32Array(gridN * gridN).fill(-1)
  for (const a of nav.areas) {
    let minGx = Infinity, maxGx = -Infinity, minGy = Infinity, maxGy = -Infinity
    for (const c of a.corners) {
      const r = toRadar(c[0], c[1])
      const gx = r.rx * gridN, gy = r.ry * gridN
      if (gx < minGx) minGx = gx; if (gx > maxGx) maxGx = gx
      if (gy < minGy) minGy = gy; if (gy > maxGy) maxGy = gy
    }
    minGx = Math.floor(minGx - 1); maxGx = Math.ceil(maxGx + 1)
    minGy = Math.floor(minGy - 1); maxGy = Math.ceil(maxGy + 1)
    for (let ty = Math.max(0, minGy); ty <= Math.min(gridN - 1, maxGy); ty++) {
      for (let tx = Math.max(0, minGx); tx <= Math.min(gridN - 1, maxGx); tx++) {
        const cx = (tx + 0.5) / gridN, cy = (ty + 0.5) / gridN
        const w = toWorld(cx, cy)
        if (pointInPoly(w.x, w.y, a.corners)) areaIdByTexel[ty * gridN + tx] = a.id
      }
    }
  }
  return { gridN, areaIdByTexel }
}

function pointInPoly(x, y, poly) {
  if (poly.length < 3) return false
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1]
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}


// 预计算：texel -> 候选 {areaId, weight}（home + 图邻区；静态一次；墙 texel 无候选 → 透明）
export function precomputeTexelBlend(nav, gridN, areaIdByTexel, toWorld, adjacencyMap, centroidMap, sigmaBlend = 800) {
  const ids = new Int32Array(gridN * gridN * MAXN).fill(-1)
  const wts = new Float32Array(gridN * gridN * MAXN).fill(0)
  for (let ty = 0; ty < gridN; ty++) {
    for (let tx = 0; tx < gridN; tx++) {
      const idx = ty * gridN + tx
      const home = areaIdByTexel[idx]
      if (home < 0) continue
      const r = { rx: (tx + 0.5) / gridN, ry: (ty + 0.5) / gridN }
      const w = toWorld(r.rx, r.ry)
      const cands = [home, ...(adjacencyMap.get(home) || [])].slice(0, MAXN)
      let sum = 0
      for (let k = 0; k < cands.length; k++) {
        const cid = cands[k]; const c = centroidMap.get(cid)
        if (!c) continue
        const d = Math.hypot(w.x - c.x, w.y - c.y)
        const wt = Math.exp(-(d * d) / (2 * sigmaBlend * sigmaBlend))
        ids[idx * MAXN + k] = cid; wts[idx * MAXN + k] = wt; sum += wt
      }
      if (sum > 1e-9) for (let k = 0; k < MAXN; k++) wts[idx * MAXN + k] /= sum
    }
  }
  return { ids, wts }
}

// 纯函数：某 texel 的 blended scalar（controlValue/magnitude），frame 提供 per-area 值
export function texelScalar(texelIndex, ids, wts, areaMap, cfg) {
  const o = texelIndex * MAXN
  let cv = 0, mag = 0, wsum = 0
  for (let k = 0; k < MAXN; k++) {
    const id = ids[o + k]
    if (id < 0) continue
    const st = areaMap.get(id)
    if (!st) continue
    const wt = wts[o + k]
    cv += wt * st.controlValue
    mag += wt * st.magnitude
    wsum += wt
  }
  if (wsum <= 1e-9) return { cv: 0, mag: 0 }
  return { cv: cv / wsum, mag: mag / wsum }
}

// 从控制帧构建 areaMap（node/lab 通用）
export function areaMapOf(frame) { return new Map(frame.areas.map((a) => [a.areaId, a])) }

// 连续 scalar → texel 颜色/alpha（cv 符号定色，mag 定 alpha；neutral/低覆盖透明）
export function scalarColor(cv, mag, cfg, breath = 0) {
  const a0 = cfg.neutralThreshold
  if (mag < a0) return [0, 0, 0, 0]
  let r = 91, g = 157, b = 217 // CT 蓝 (cv>0)
  if (cv < 0) { r = 230; g = 165; b = 74 } // T 橙
  const band = Math.abs(cv)
  if (band < cfg.contestRatio) { r = 226; g = 165; b = 74 } // 争夺琥珀（窄带）
  const alphaMul = 1 + 0.04 * Math.sin(breath) // 极轻呼吸（仅幅度微调，不掩盖静态；数据仍驱动）
  const alpha = Math.min(0.34, (0.06 + 0.28 * mag) * alphaMul)
  return [r, g, b, Math.round(alpha * 255)]
}

// 从帧构建 typed-array 值表（O(1) 数组访问，避免每 texel Map.get）
export function buildValues(frame, maxId) {
  const cv = new Float32Array(maxId + 1)
  const mag = new Float32Array(maxId + 1)
  for (const a of frame.areas) { if (a.areaId <= maxId) { cv[a.areaId] = a.controlValue; mag[a.areaId] = a.magnitude } }
  return { cv, mag }
}

// 构建逐 texel 连续标量场（blend）+ 可走 mask（供 fog 渲染 + marching-squares contour）
// out 可选：复用预先分配的 {cv,mag,mask}（E6A-BUFFER-POOL 复用 scratch，降低端点构建期间的分配/GC 压力）
export function computeScalarGrid(ids, wts, values, gridN, out = null) {
  const cv = (out && out.cv) || new Float32Array(gridN * gridN)
  const mag = (out && out.mag) || new Float32Array(gridN * gridN)
  const mask = (out && out.mask) || new Uint8Array(gridN * gridN)
  if (out) { cv.fill(0); mag.fill(0); mask.fill(0) } // 复用 scratch 须清零（否则残留上次值）
  const { cv: cvT, mag: magT } = values
  for (let i = 0; i < gridN * gridN; i++) {
    const o = i * MAXN
    if (ids[o] < 0) continue
    mask[i] = 1
    let c = 0, m = 0, ws = 0
    for (let k = 0; k < MAXN; k++) {
      const id = ids[o + k]
      if (id < 0) continue
      const wt = wts[o + k]
      c += wt * cvT[id]; m += wt * magT[id]; ws += wt
    }
    if (ws > 1e-9) { cv[i] = c / ws; mag[i] = m / ws }
  }
  return { cv, mag, mask }
}

// 渲染影响雾（weak；默认弱/克制；contour 为主视觉）
export function renderScalarField(gridCtx, gridN, grid, cfg, breath = 0) {
  const img = gridCtx.__fieldImg || (gridCtx.__fieldImg = gridCtx.createImageData(gridN, gridN))
  const d = img.data
  const alphaMul = 1 + 0.04 * Math.sin(breath)
  for (let i = 0; i < gridN * gridN; i++) {
    const o = i * 4
    if (!grid.mask[i]) { d[o + 3] = 0; continue }
    const cv = grid.cv[i], mag = grid.mag[i]
    if (mag < cfg.neutralThreshold) { d[o + 3] = 0; continue }
    let r = 91, g = 157, b = 217
    if (cv < 0) { r = 230; g = 165; b = 74 }
    if (Math.abs(cv) < cfg.contestRatio) { r = 226; g = 165; b = 74 }
    const alpha = Math.min(0.3, (0.12 + 0.18 * mag) * alphaMul) // 克制 0.12–0.30；控制沿走廊延伸、远端 neutral 透明
    d[o] = r; d[o + 1] = g; d[o + 2] = b; d[o + 3] = Math.round(alpha * 255)
  }
  gridCtx.putImageData(img, 0, 0)
}

// ---- 零交叉 isoline/frontier ----
// 预计算：每条邻接边共享边端点（静态几何，一次）→ [ {a,b,x1,y1,x2,y2} ]
export function precomputeFrontierEdges(nav) {
  const area = new Map(nav.areas.map((a) => [a.id, a]))
  const out = []
  for (const a of nav.areas) {
    for (const nb of a.adjacency || []) {
      if (nb <= a.id) continue
      const shared = sharedEdge(area.get(a.id), area.get(nb))
      if (shared) out.push({ a: a.id, b: nb, x1: shared[0][0], y1: shared[0][1], x2: shared[1][0], y2: shared[1][1] })
    }
  }
  return out
}
// 每帧：给定预计算边 + 控制帧，返回跨 contest band 的前线段（窄、连续、随时间迁移）
export function computeFrontier(edges, areaMap, cfg) {
  const segs = []
  for (const e of edges) {
    const A = areaMap.get(e.a), B = areaMap.get(e.b)
    if (!A || !B) continue
    const cvA = A.controlValue, cvB = B.controlValue
    const band = cfg.frontierBand || 0.05
    if (!((cvA >= band && cvB <= -band) || (cvA <= -band && cvB >= band))) continue
    segs.push({ a: e.a, b: e.b, x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2, cv: (cvA + cvB) / 2, mag: Math.max(A.magnitude, B.magnitude) })
  }
  return segs
}

function sharedEdge(ga, gb) {
  const as = new Map(), bs = new Map()
  for (const p of ga.corners) as.set(p[0] + ',' + p[1], p)
  for (const p of gb.corners) bs.set(p[0] + ',' + p[1], p)
  const common = []
  for (const [k, p] of as) if (bs.has(k)) common.push(p)
  return common.length >= 2 ? [common[0], common[1]] : null
}

// texel -> 连通分量 id（用于 marching squares 只允许同分量合法连通，绝不跨墙）
export function buildTexelComp(areaIdByTexel, compOfArea, gridN) {
  const c = new Uint16Array(gridN * gridN)
  for (let i = 0; i < gridN * gridN; i++) { const id = areaIdByTexel[i]; c[i] = id < 0 ? 0xffff : (compOfArea.get(id) ?? 0xf000) }
  return c
}

// masked 平滑（仅同可走+同连通分量 texel 平均；不跨墙/洞/分量；非全局 blur）→ 让零等值线连续可读
export function smoothScalarCv(cv, mask, comp, gridN, iters = 2, work = null) {
  let src = cv, tmp = work || new Float32Array(cv.length)
  const offs = [[-1, 0], [1, 0], [0, -1], [0, 1]]
  for (let it = 0; it < iters; it++) {
    for (let y = 0; y < gridN; y++) {
      for (let x = 0; x < gridN; x++) {
        const i = y * gridN + x
        if (!mask[i]) { tmp[i] = src[i]; continue }
        let s = src[i], n = 1
        for (const [dx, dy] of offs) {
          const nx = x + dx, ny = y + dy
          if (nx < 0 || ny < 0 || nx >= gridN || ny >= gridN) continue
          const j = ny * gridN + nx
          if (!mask[j] || comp[j] !== comp[i]) continue
          s += src[j]; n++
        }
        tmp[i] = s / n
      }
    }
    const t = src; src = tmp; tmp = t
  }
  return src
}

// 连续零等值线（marching squares）：四角必须同连通分量（跨墙/洞/地图外不连）；case 5/10 asymptotic decider（无 X 交叉）。
// level=0 为 active 前线；返回 segments [{x1,y1,x2,y2,active}]，active = 交叉处 magnitude >= threshold（双方实际影响）。
export function extractContour(cv, mag, mask, texelComp, gridN, level, magThreshold) {
  const segs = []
  const lerp = (a, b, va, vb) => { const f = (level - va) / (vb - va); return a + (b - a) * f }
  for (let y = 0; y < gridN - 1; y++) {
    for (let x = 0; x < gridN - 1; x++) {
      const i00 = y * gridN + x, i10 = i00 + 1, i01 = i00 + gridN, i11 = i01 + 1
      if (!mask[i00] || !mask[i10] || !mask[i01] || !mask[i11]) continue
      if (texelComp[i00] !== texelComp[i10] || texelComp[i00] !== texelComp[i01] || texelComp[i00] !== texelComp[i11]) continue // 同连通分量限制
      const v00 = cv[i00], v10 = cv[i10], v01 = cv[i01], v11 = cv[i11]
      let ci = 0
      if (v00 > level) ci |= 1
      if (v10 > level) ci |= 2
      if (v11 > level) ci |= 4
      if (v01 > level) ci |= 8
      if (ci === 0 || ci === 15) continue
      const top = v00 > level !== v10 > level ? [lerp(x, x + 1, v00, v10), y] : null
      const right = v10 > level !== v11 > level ? [x + 1, lerp(y, y + 1, v10, v11)] : null
      const bottom = v11 > level !== v01 > level ? [lerp(x, x + 1, v11, v01), y + 1] : null
      const left = v01 > level !== v00 > level ? [x, lerp(y, y + 1, v01, v00)] : null
      const pts = [top, right, bottom, left].filter(Boolean)
      let pairs
      if (ci === 5 || ci === 10) {
        // asymptotic decider：用中心采样（四角均值）决定配对，避免 X 交叉
        const center = (v00 + v10 + v01 + v11) / 4
        if ((center > level) === ((ci & 1) > 0)) pairs = [[left, bottom], [top, right]]
        else pairs = [[left, top], [bottom, right]]
      } else {
        pairs = [[pts[0], pts[1]]]
      }
      for (const [p1, p2] of pairs) {
        if (!p1 || !p2) continue
        const a = magAt(i00, i10, i01, i11, [p1], mag)
        segs.push({ x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1], active: magAtCell(i00, i10, i01, i11, mag) >= magThreshold })
      }
    }
  }
  return segs
}
function magAtCell(i00, i10, i01, i11, mag) { return Math.max(mag[i00], mag[i10], mag[i01], mag[i11]) }
function magAt(i00, i10, i01, i11, pts, mag) { return magAtCell(i00, i10, i01, i11, mag) }

// 把 segment 稳定 stitch 成 polyline；过滤极短噪点（< minLen）；不伪造缺失区域。
export function stitchContour(segs, minLen = 2.0) {
  const edges = segs.map((s) => ({ a: s.x1 + ',' + s.y1, b: s.x2 + ',' + s.y2, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2 }))
  const adj = new Map()
  const key = (p) => p[0].toFixed(3) + ',' + p[1].toFixed(3)
  const push = (e, from) => { const k = key(from); if (!adj.has(k)) adj.set(k, []); adj.get(k).push(e) }
  for (const e of edges) { push(e, [e.x1, e.y1]); push(e, [e.x2, e.y2]) }
  const used = new Set(); const lines = []
  for (const e of edges) {
    if (used.has(e)) continue
    const poly = [[e.x1, e.y1], [e.x2, e.y2]]; used.add(e)
    for (const end of [0, 1]) {
      let cur = poly[end]
      for (;;) {
        const list = adj.get(key(cur)) || []
        let nextE = null
        for (const c of list) if (!used.has(c)) { nextE = c; break }
        if (!nextE) break
        const pts = [[nextE.x1, nextE.y1], [nextE.x2, nextE.y2]]
        const other = Math.abs(pts[0][0] - cur[0]) + Math.abs(pts[0][1] - cur[1]) < 0.1 ? pts[1] : pts[0]
        if (end === 0) poly.unshift(other); else poly.push(other)
        used.add(nextE); cur = other
      }
    }
    const len = polyLen(poly)
    if (len >= minLen && poly.length >= 2) lines.push(poly)
  }
  return lines
}
function polyLen(poly) { let s = 0; for (let i = 1; i < poly.length; i++) { const dx = poly[i][0] - poly[i - 1][0], dy = poly[i][1] - poly[i - 1][1]; s += Math.hypot(dx, dy) } return s }
