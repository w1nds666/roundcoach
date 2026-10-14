// 6R21F-E3 NavMesh 图级控制前线：以相邻 NavArea 的控制符号/置信度变化 + 合法共享边为来源，
// 在同一连通分量内插值边界点，再沿合法邻接组装/简化成少量有方向连续 polyline。
// 替代 raster marching-squares 在 GRID_N=128 上产生 41 个短碎片的问题。不跨墙/洞/组件/地图边界。
import { areaCentroid } from './model.js'

// 对一条合法邻接边 (a,b)，若两侧控制符号/类不同 → 产出边界点（沿质心按 |cv| 权重插值，靠近更强一侧）。
// 直接消费定长 typed arrays cv/mag（index=areaId），不建 2544 对象 Map（避免 GC 尖峰）。
function boundaryPoint(a, b, cv, mag, centroids, band, threshold) {
  const cvA = cv[a] ?? 0, cvB = cv[b] ?? 0
  const cls = (v) => (v >= band ? 'ct' : v <= -band ? 't' : 'n')
  const ca = cls(cvA), cb = cls(cvB)
  if (ca === cb) return null
  const wa = Math.abs(cvA) / ((Math.abs(cvA) + Math.abs(cvB)) || 1)
  const ca2 = centroids.get(a), cb2 = centroids.get(b)
  if (!ca2 || !cb2) return null
  return { a, b, x: ca2.x + (cb2.x - ca2.x) * wa, y: ca2.y + (cb2.y - ca2.y) * wa, active: (mag[a] ?? 0) >= threshold && (mag[b] ?? 0) >= threshold }
}

// 组装：边界点按"共享同一 area + 空间相邻"连边 → 连通分量 → 最近邻排序成 polyline。
export function computeAreaFrontier({ areas, cv, mag, centroids, adjacencyMap, band = 0.05, connectDist, threshold = 0.22 }) {
  const pts = []
  for (const [a, adj] of adjacencyMap) for (const b of adj) if (a < b) { const p = boundaryPoint(a, b, cv, mag, centroids, band, threshold); if (p) pts.push(p) }
  if (!pts.length) return { lines: [] }
  // 平均边长（质心距离）作为连接阈值基准
  let es = 0, en = 0
  for (const [a, adj] of adjacencyMap) for (const b of adj) if (a < b) { const A = centroids.get(a), B = centroids.get(b); if (A && B) { es += Math.hypot(A.x - B.x, A.y - B.y); en++ } }
  const avgEdge = en ? es / en : 1000
  const D = connectDist || avgEdge * 1.4
  // 点-点连边：共享 area 且空间近
  const n = pts.length
  const adj = Array.from({ length: n }, () => [])
  const areaPts = new Map()
  pts.forEach((p, i) => { for (const id of [p.a, p.b]) { if (!areaPts.has(id)) areaPts.set(id, []); areaPts.get(id).push(i) } })
  for (const [, list] of areaPts) {
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const A = pts[list[i]], B = pts[list[j]]
      const d = Math.hypot(A.x - B.x, A.y - B.y)
      if (d < D) { adj[list[i]].push(list[j]); adj[list[j]].push(list[i]) }
    }
  }
  // 访问 + 组件追踪（最近邻排序）
  const visited = new Array(n).fill(false)
  const lines = []
  for (let s = 0; s < n; s++) {
    if (visited[s]) continue
    const comp = []; const st = [s]; visited[s] = true
    while (st.length) { const cur = st.pop(); comp.push(cur); for (const nx of adj[cur]) if (!visited[nx]) { visited[nx] = true; st.push(nx) } }
    const start = comp.find((i) => adj[i].length <= 1) ?? comp[0]
    const idxLine = []
    const used = new Set(); let cur = start
    while (cur != null && !used.has(cur)) {
      used.add(cur); idxLine.push(cur)
      let bn = null, bd = Infinity
      for (const nx of adj[cur]) if (!used.has(nx)) { const d = Math.hypot(pts[cur].x - pts[nx].x, pts[cur].y - pts[nx].y); if (d < bd) { bd = d; bn = nx } }
      cur = bn
    }
    if (idxLine.length >= 2) {
      const poly = idxLine.map((idx) => ({ x: pts[idx].x, y: pts[idx].y }))
      const active = idxLine.every((idx) => pts[idx].active)
      lines.push({ pts: simplify(poly), active })
    }
  }
  return { lines: lines.filter((l) => l.pts.length >= 2) }
}

function simplify(line) {
  const out = [line[0]]
  for (let i = 1; i < line.length - 1; i++) {
    const a = out[out.length - 1], b = line[i], c = line[i + 1]
    const d = perpendicularDist(a, b, c)
    if (d > 12) out.push(b) // 保留曲率明显点
  }
  out.push(line[line.length - 1])
  return out
}
function perpendicularDist(a, b, c) {
  const dx = c.x - a.x, dy = c.y - a.y
  const L = Math.hypot(dx, dy) || 1
  return Math.abs((b.x - a.x) * dy - (b.y - a.y) * dx) / L
}
