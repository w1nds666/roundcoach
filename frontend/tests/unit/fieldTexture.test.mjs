// 6R21F Milestone D2 渲染器纯函数红绿测试（node --test）。
// 关键：相邻 area 边界 scalar 连续；屏幕近但图不相邻/隔墙两侧严格不串色；真实 Mirage 固定墙对；
//      已知交战时刻存在非零 frontier 且窄；同帧确定性。
import { test } from 'node:test'
import assert from 'node:assert'
import { buildAreaGrid, precomputeTexelBlend, texelScalar, areaMapOf, scalarColor, computeFrontier, precomputeFrontierEdges, MAXN } from '../../src/map-control/fieldTexture.js'
import { createGraph, addNode, addEdge, computeControlFrame, areaCentroid } from '../../src/map-control/model.js'

const LIM = 5120
const toRadar = (x, y) => ({ rx: x / LIM, ry: y / LIM })
const toWorld = (rx, ry) => ({ x: rx * LIM, y: ry * LIM })
const CONF = { sigma: 600, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }
const gridN = 64

// 合成：A-B 共享边(相邻)；C-D 共享边；B-C 几何贴近但**无边**（墙）；B-C 不同组件。
const nav = {
  source: { sha256: 'synth' },
  areas: [
    { id: 1, corners: [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [0, 1000, 0]], adjacency: [2] },
    { id: 2, corners: [[1000, 0, 0], [2000, 0, 0], [2000, 1000, 0], [1000, 1000, 0]], adjacency: [1] },
    { id: 3, corners: [[2000, 0, 0], [3000, 0, 0], [3000, 1000, 0], [2000, 1000, 0]], adjacency: [4] },
    { id: 4, corners: [[3000, 0, 0], [4000, 0, 0], [4000, 1000, 0], [3000, 1000, 0]], adjacency: [3] },
  ],
}
function buildGraph() {
  const g = createGraph()
  for (const a of nav.areas) { const c = areaCentroid(a); addNode(g, a.id, c.x, c.y) }
  for (const a of nav.areas) for (const nb of a.adjacency) addEdge(g, a.id, nb) // need addEdge
  return g
}
const adjacencyMap = new Map(nav.areas.map((a) => [a.id, new Set(a.adjacency)]))
const centroidMap = new Map(nav.areas.map((a) => { const c = areaCentroid(a); return [a.id, { x: c.x, y: c.y }] }))
const g = buildGraph()
const fr = computeControlFrame(g, [{ team: 'CT', areaId: 1, alive: true }], CONF)
const areaMap = areaMapOf(fr)
const { areaIdByTexel } = buildAreaGrid(nav, gridN, toRadar, toWorld)
const blend = precomputeTexelBlend(nav, gridN, areaIdByTexel, toWorld, adjacencyMap, centroidMap, 800)
function texelAt(x, y) { const r = toRadar(x, y); return Math.floor(r.ry * gridN) * gridN + Math.floor(r.rx * gridN) }

test('相邻边界 scalar 连续（无硬台阶）：A/B 共享边两侧 blended 值接近', () => {
  // x=990 (A 侧) 与 x=1010 (B 侧) 贴近共享边 x=1000 → blended cv 都要靠近 A↔B 的中间
  const sA = texelScalar(texelAt(900, 500), blend.ids, blend.wts, areaMap, CONF)
  const sEdgeA = texelScalar(texelAt(990, 500), blend.ids, blend.wts, areaMap, CONF)
  const sEdgeB = texelScalar(texelAt(1010, 500), blend.ids, blend.wts, areaMap, CONF)
  const sB = texelScalar(texelAt(1100, 500), blend.ids, blend.wts, areaMap, CONF)
  // A 与 B 同为 CT 且值相近 → 四个采样 cv 均>0 且相邻采样之间差值小（无硬台阶）
  assert.ok(sA.cv > 0 && sB.cv > 0)
  assert.ok(Math.abs(sEdgeA.cv - sEdgeB.cv) < Math.abs(sA.cv - sB.cv) * 0.9, '边界两侧应连续（值相近）')
})

test('隔墙硬断：C(不可达) texel 不被 B 的 CT 染色（B 非 C 候选）', () => {
  const sB = texelScalar(texelAt(1900, 500), blend.ids, blend.wts, areaMap, CONF) // B 区（CT）
  const sC = texelScalar(texelAt(2100, 500), blend.ids, blend.wts, areaMap, CONF) // C 区（wall 对面，不可达）
  assert.ok(sB.mag > CONF.neutralThreshold, 'B 应被控制')
  assert.ok(sC.mag < CONF.neutralThreshold, 'C（隔墙不可达）应 neutral/透明，不串 B 的色')
  // C 的候选集不含 B（2）
  const oC = texelAt(2100, 500) * MAXN
  assert.ok(!Array.from({ length: MAXN }, (_, k) => blend.ids[oC + k]).includes(2), 'C 的 blend 候选不得含 B')
})

test('真实 Mirage 固定墙对：1685 vs 1687（不同连通分量、世界距离≈3）不互相染色', async () => {
  const nav = JSON.parse((await import('node:fs')).readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
  const adj = new Map(nav.areas.map((a) => [a.id, new Set(a.adjacency)]))
  const cent = new Map(nav.areas.map((a) => { const c = areaCentroid(a); return [a.id, { x: c.x, y: c.y }] }))
  const a = cent.get(1685), b = cent.get(1687)
  assert.ok(a && b, '存在 area 1685/1687')
  const d = Math.hypot(a.x - b.x, a.y - b.y)
  assert.ok(d < 60, `墙对世界距离应很近（实际 ${Math.floor(d)}）`)
  assert.ok(!adj.get(1685).has(1687) && !adj.get(1687).has(1685), '1685/1687 图不相邻（隔墙）')
  // 两者不同连通分量
  const comp = (id) => { const seen = new Set([id]); const st = [id]; while (st.length) { const cur = st.pop(); for (const nb of (adj.get(cur) || [])) if (!seen.has(nb)) { seen.add(nb); st.push(nb) } } return seen }
  assert.ok(!comp(1685).has(1687), '1685/1687 不同连通分量（墙隔离）')
})

test('交战时刻存在非零 frontine（零交叉共享边），端点走可行走区', () => {
  const edges = precomputeFrontierEdges(nav)
  // 构造 CT@1 与 T@2（同组件、相邻）→ 1-2 共享边跨零 → 前线
  const g2 = createGraph()
  for (const an of nav.areas) { const c = areaCentroid(an); addNode(g2, an.id, c.x, c.y) }
  for (const an of nav.areas) for (const nb of an.adjacency) addEdge(g2, an.id, nb)
  const fr2 = computeControlFrame(g2, [{ team: 'CT', areaId: 1, alive: true }, { team: 'T', areaId: 2, alive: true }], { ...CONF, sigma: 3000 })
  const segs2 = computeFrontier(edges, areaMapOf(fr2), CONF)
  assert.ok(segs2.length > 0, '跨零时应有非零前线')
  for (const s of segs2) {
    assert.ok(Number.isFinite(s.x1) && Number.isFinite(s.x2), '前线端点应有限')
  }
})

test('同帧确定性 + buildAreaGrid 稳定', () => {
  const a1 = texelScalar(texelAt(900, 500), blend.ids, blend.wts, areaMap, CONF)
  const a2 = texelScalar(texelAt(900, 500), blend.ids, blend.wts, areaMap, CONF)
  assert.deepEqual({ cv: a1.cv, mag: a1.mag }, { cv: a2.cv, mag: a2.mag })
  const g2 = buildAreaGrid(nav, gridN, toRadar, toWorld)
  assert.deepEqual(Array.from(areaIdByTexel), Array.from(g2.areaIdByTexel))
})
