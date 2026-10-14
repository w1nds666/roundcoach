import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createPipeline } from '../../src/map-control/pipeline.js'

const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('public/demo/6r21-round-sample.json', 'utf8'))
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }

test('landing truth: kill event immediately removes victim from control inputs', () => {
  const pipeline = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const before = pipeline.playersAt(56.66).find((player) => player.uid === 'T3')
  const atDeath = pipeline.playersAt(56.67).find((player) => player.uid === 'T3')
  assert.equal(before.alive, true)
  assert.equal(atDeath.alive, false)
  assert.equal(atDeath.deathAt, 56.67)
})

test('landing truth: only actual survivors drive the 00:58 frontier', () => {
  const pipeline = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const alive = pipeline.playersAt(58).filter((player) => player.alive).map((player) => player.uid).sort()
  assert.deepEqual(alive, ['CT1', 'T4'])
})

test('landing truth: no opposing frontier remains after the final death', () => {
  const pipeline = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
  const alive = pipeline.playersAt(60.92).filter((player) => player.alive).map((player) => player.uid)
  assert.deepEqual(alive, ['T4'])
  const contour = pipeline.contourAt(60.92)
  assert.equal(contour.activeCount, 0)
  assert.equal(contour.lowCount, 0)
})
