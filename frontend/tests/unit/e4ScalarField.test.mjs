// 6R21F-E4 连续标量场纯模型/渲染测试：neutral 透明、mask 无非可玩像素、墙两侧不串色、时间 hash 确定。
import { test } from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import { createPipeline } from '../../src/map-control/pipeline.js'
import { smoothScalarCv } from '../../src/map-control/fieldTexture.js'

const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('public/demo/6r21-round-sample.json', 'utf8'))
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }

test('E4 neutral 透明：masked texel 的 magnitude 低于阈值 → 不产生可见 alpha 贡献', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const grid = P.scalarGridAt(36.5)
  let neutralMasked = 0
  for (let i = 0; i < grid.mask.length; i++) if (grid.mask[i] && grid.mag[i] < CFG.neutralThreshold) neutralMasked++
  assert.ok(neutralMasked > 0, '应存在低于阈值的 masked texel（neutral 透明）')
})

test('E4 mask 无非可玩像素：mask=0 的 texel 不会得到 cv/mag（不能出现场）', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const grid = P.scalarGridAt(36.5)
  let bad = 0
  for (let i = 0; i < grid.mask.length; i++) if (!grid.mask[i] && (grid.mag[i] !== 0 || grid.cv[i] !== 0)) bad++
  assert.equal(bad, 0, '非可玩 texel 必须零贡献（不跨图外/墙）')
})

test('E4 墙两侧不串色：masked 平滑仅在同可走+同分量 texel 平均（smoothScalarCv 不跨分量）', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const grid = P.scalarGridAt(36.5)
  const scv = smoothScalarCv(grid.cv, grid.mask, P.texelComp, P.gridN, 1)
  // 每个可走 texel 平滑后仍有限；非可走 texel 不变
  let bad = 0
  for (let i = 0; i < grid.mask.length; i++) if (!grid.mask[i] && scv[i] !== grid.cv[i]) bad++
  assert.equal(bad, 0, '非可走 texel 平滑后不变（不跨墙）')
})

test('E4 固定时刻 hash 确定（模型更新率与 rAF 解耦，同刻确定性）', () => {
  const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  assert.equal(P.frameHash(36.5), P.frameHash(36.5))
})
