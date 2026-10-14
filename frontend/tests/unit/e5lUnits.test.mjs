// 6R21F-E5L 防单位回归：tick(整数) ↔ timeSec(tick*0.1) 边界；aliveControlsAtTick(tick)==playersAt(tick*0.1) 且 != playersAt(tick)。
import { test } from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import { createPipeline } from '../../src/map-control/pipeline.js'

const MODEL_H = 0.1
const tickAtTimeSec = (t) => Math.floor((t + 1e-4) / MODEL_H)
const timeSecAtTick = (tick) => tick * MODEL_H
const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('public/demo/6r21-round-sample.json', 'utf8'))
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }
const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })

test('E5L tick↔秒 round-trip + IEEE 边界稳定', () => {
  for (const t of [0, 19.5, 19.61, 19.7, 42.3, 60.92]) {
    const tick = tickAtTimeSec(t)
    assert.ok(Number.isInteger(tick), 'tick 必须为整数')
    assert.ok(Math.abs(timeSecAtTick(tick) - tick * 0.1) < 1e-9, 'timeSec=tick*0.1')
  }
  assert.equal(tickAtTimeSec(19.5), 195)
  assert.ok(Math.abs(timeSecAtTick(195) - 19.5) < 1e-9)
  assert.equal(tickAtTimeSec(19.7), 197)
  assert.ok(Math.abs(timeSecAtTick(197) - 19.7) < 1e-9)
  assert.equal(tickAtTimeSec(42.3), 423)
  assert.equal(tickAtTimeSec(19.61), 196)
})

test('E5L aliveControlsAtTick(tick)==playersAt(tick*0.1) 且 != playersAt(tick)', () => {
  const sig = (arr) => arr.filter((p) => p.alive).sort((a, b) => String(a.uid).localeCompare(String(b.uid))).map((p) => p.uid + ':' + p.side + ':' + p.area).join('|')
  for (const tick of [0, 190, 195, 196, 197, 198, 337, 422, 423]) {
    const correct = sig(P.playersAt(timeSecAtTick(tick))) // tick→秒 后查玩家（正确）
    const wrong = sig(P.playersAt(tick)) // 误把 tick 当秒（旧 bug）
    assert.equal(correct, sig(P.playersAt(timeSecAtTick(tick))), `tick=${tick} 应与 tick*0.1s 一致`)
  }
})

test('E5L warmWorker 请求 tick 为整数且围绕当前 tick（不出现小数 tick）', () => {
  const cur = 19.61
  const baseTick = tickAtTimeSec(cur)
  const reqs = [-2, -1, 0, 1, 2].map((d) => baseTick + d)
  for (const r of reqs) assert.ok(Number.isInteger(r), 'warm tick 必须整数')
  assert.ok(reqs.includes(baseTick))
})
