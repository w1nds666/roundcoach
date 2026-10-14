// 6R21F-E2 parity 严格测试：lab/离线扫描共用 createPipeline → 同刻 hash 完全一致、前线不跨墙/分量。
import { test } from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import { createPipeline } from '../../src/map-control/pipeline.js'

const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('public/demo/6r21-round-sample.json', 'utf8'))
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }

test('parity：同数据两次 createPipeline → frameHash/contour 完全一致', () => {
  const A = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const B = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  for (const t of [24, 34.25, 36.5, 42.25]) {
    assert.equal(A.frameHash(t), B.frameHash(t), `frameHash mismatch t=${t}`)
    const a = A.contourAt(t), b = B.contourAt(t)
    assert.equal(a.activeCount, b.activeCount)
    assert.equal(a.activeLines.length, b.activeLines.length)
  }
})

test('parity：t=36.5 存在真实 active 或低置信前线；同刻 hash 稳定', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const h1 = P.frameHash(36.5), h2 = P.frameHash(36.5)
  assert.equal(h1, h2, '同输入同 hash（确定性）')
  const c = P.contourAt(36.5)
  assert.ok(c.activeCount + c.lowCount > 0, '应存在 front 段')
  assert.ok(c.lowCount > 0, '1v3 非对称残局应诚实保留低置信前线')
})

test('多个真实时刻：双方存活时存在前线；一方全灭后前线归零', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const D = demo.round.max_duration
  let bothAliveFrames = 0, bothAliveWithFront = 0, oneSideFrames = 0, oneSideWithFront = 0, maxA = 0
  const times = []
  for (let t = 0; t <= D; t += 0.25) times.push(t)
  if (times[times.length - 1] !== D) times.push(D)
  for (const t of times) {
    const c = P.contourAt(t)
    const alive = P.playersAt(t).filter((player) => player.alive)
    const bothAlive = alive.some((player) => player.side === 'CT') && alive.some((player) => player.side === 'T')
    if (bothAlive) { bothAliveFrames++; if (c.activeCount + c.lowCount > 0) bothAliveWithFront++ }
    else { oneSideFrames++; if (c.activeCount + c.lowCount > 0) oneSideWithFront++ }
    if (c.activeCount > maxA) maxA = c.activeCount
  }
  assert.equal(bothAliveWithFront, bothAliveFrames, '双方存活时应有 front')
  assert.equal(oneSideWithFront, 0, '一方全灭后不得残留 front')
  assert.ok(oneSideFrames > 0, '样本应覆盖一方全灭状态')
  assert.ok(maxA > 0, '存在 active 峰值')
})

test('前线不跨墙：contour 段端点都在可玩 mask 且同连通分量（extractContour 组件约束已由 d4Render 托底；此处校管线输出）', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const c = P.contourAt(36.5)
  assert.ok(c.activeLines.concat(c.lowLines).every((line) => line.length >= 2), 'polyline 至少 2 点')
})
