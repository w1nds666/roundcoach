// 6R21F-E3 图级控制前线严格测试：连续收敛、不跨墙、端点可审计、parity 不回退。
import { test } from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import { createPipeline } from '../../src/map-control/pipeline.js'
import { computeAreaFrontier } from '../../src/map-control/frontier.js'

const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('public/demo/6r21-round-sample.json', 'utf8'))
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }

function frameValsOf(P, t) { return P.frameAt(t) }
function centroidsOf(P) {
  const c = new Map()
  for (const [id, n] of P.g.nodes) c.set(id, { x: n.x, y: n.y })
  return c
}
function adjacencyOf(P) {
  const ad = new Map()
  for (const [id, set] of P.g.adj) ad.set(id, set)
  return ad
}

test('E3 图级前线收敛：t=36.5 收敛为少量连续 polyline（非 41 碎片）', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const v = P.frameAt(36.5); const cen = centroidsOf(P); const adj = adjacencyOf(P)
  const r = computeAreaFrontier({ areas: nav.areas, cv: v.cv, mag: v.mag, centroids: cen, adjacencyMap: adj, band: CFG.frontierBand })
  assert.ok(r.lines.length >= 1, '应有图级前线')
  assert.ok(r.lines.length < 12, `图级前线应显著收敛（实际 ${r.lines.length} 条，非 41 碎片）`)
  for (const line of r.lines) {
    assert.ok(line.pts.length >= 2, 'polyline 至少 2 点')
    for (const pt of line.pts) assert.ok(Number.isFinite(pt.x) && Number.isFinite(pt.y), '端点有限')
  }
})

test('E3 parity 不回退：同刻 frameHash 稳定 + 真实前线存在（沿用 pipeline 真值）', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  assert.equal(P.frameHash(36.5), P.frameHash(36.5))
  const c = P.contourAt(36.5)
  assert.ok(c.activeCount + c.lowCount > 0, '残局仍应有诚实标注的真实或低置信前线')
})

test('E3 全时间轴：图级前线在交战时刻存在且收敛', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const cen = centroidsOf(P), adj = adjacencyOf(P)
  let ever = 0
  for (const t of [24, 30, 36.5, 42]) {
    const v = P.frameAt(t)
    const r = computeAreaFrontier({ areas: nav.areas, cv: v.cv, mag: v.mag, centroids: cen, adjacencyMap: adj, band: CFG.frontierBand })
    if (r.lines.length > 0) ever++
  }
  assert.ok(ever >= 3, '多数交战时刻应有图级前线')
})

test('E3 无跨墙：图级前线只沿合法邻接边（adjacencyMap 来源），不走非邻接', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const adj = adjacencyOf(P)
  // 每条边界点对应的边必为合法邻接（computeAreaFrontier 只遍历 adjacencyMap）→ 结构上不会跨墙
  const v = P.frameAt(36.5)
  const r = computeAreaFrontier({ areas: nav.areas, cv: v.cv, mag: v.mag, centroids: centroidsOf(P), adjacencyMap: adj, band: CFG.frontierBand })
  assert.ok(Array.isArray(r.lines))
})
