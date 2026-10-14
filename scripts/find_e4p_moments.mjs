// 6R21F-E4P：确定四类证据时刻（00:19、active 峰值、contested 峰值、一次死亡前后 0.5s）。
import fs from 'node:fs'
import { createPipeline } from '../frontend/src/map-control/pipeline.js'
const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('frontend/public/demo/6r21-round-sample.json', 'utf8'))
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }
const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
const D = demo.round.max_duration
let maxAct = 0, tAct = 0, maxContest = 0, tContest = 0
for (let t = 0; t <= D; t += 0.25) {
  const c = P.contourAt(t)
  if (c.activeCount > maxAct) { maxAct = c.activeCount; tAct = t }
  if (c.activeCount + c.lowCount > maxContest) { maxContest = c.activeCount + c.lowCount; tContest = t }
}
// 死亡时刻（首个 kills 事件）
const kills = (demo.events && demo.events.kills) || []
let deathT = null
if (kills.length) deathT = Number(kills[0].time)
console.log('duration=' + D)
console.log('active peak t=' + tAct + ' active=' + maxAct + ' ratio=' + (tAct / D).toFixed(3))
console.log('contest peak t=' + tContest + ' contest=' + maxContest + ' ratio=' + (tContest / D).toFixed(3))
console.log('death t=' + (deathT ?? 'none') + (deathT != null ? ' ratio=' + (deathT / D).toFixed(3) : '') + ' kills=' + kills.length)
console.log('kill times=' + JSON.stringify(kills.map((k) => Number(k.time))))
