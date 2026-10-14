// 6R21F-E2：离线扫描改用**统一管线** createPipeline（与 /map-control-lab 同一数据管线）。
// 用法：node scripts/scan_6r21f_contour.mjs
import fs from 'node:fs'
import { createPipeline } from '../frontend/src/map-control/pipeline.js'
import { stitchContour } from '../frontend/src/map-control/fieldTexture.js'

const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('frontend/public/demo/6r21-round-sample.json', 'utf8'))
const cfg = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }
const P = createPipeline({ nav, sample: demo, cfg, opts: { gridN: 128 } })
const D = demo.round.max_duration

let maxActive = 0, tMaxA = 0, maxAny = 0, tMaxAny = 0, withC = 0, total = 0
for (let t = 0; t <= D; t += 0.25) {
  const c = P.contourAt(t)
  total++
  if (c.activeCount + c.lowCount > 0) withC++
  if (c.activeCount > maxActive) { maxActive = c.activeCount; tMaxA = t }
  if (c.activeCount + c.lowCount > maxAny) { maxAny = c.activeCount + c.lowCount; tMaxAny = t }
}
console.log('=== E2 统一管线扫描 ===')
console.log('moments with contour:', withC, '/', total)
console.log('max active segs:', maxActive, '@ t=', tMaxA, ' max any segs:', maxAny, '@ t=', tMaxAny)
// parity 关键时刻
for (const t of [tMaxA, 24, 34.25, 48]) {
  const c = P.contourAt(t)
  // 短段连通性诊断：不同 minLen 的 stitch 长度
  const st1 = stitchContour(c.active, 0.5).length
  const st2 = stitchContour(c.active, 1.5).length
  console.log(`  t=${t} active=${c.activeCount} low=${c.lowCount} activeLines(min3)=${c.activeLines.length} activeLines(min0.5)=${st1} activeLines(min1.5)=${st2} lowLines=${c.lowLines.length} frameHash=${P.frameHash(t)}`)
}
console.log('parity stable:', P.frameHash(tMaxA) === P.frameHash(tMaxA), ' (frameAt 同输入同 hash)')
