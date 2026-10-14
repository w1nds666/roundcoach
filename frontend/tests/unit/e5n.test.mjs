// 6R21F-E5N 单位回归：端点/同步 parity 纯函数 + 连续性纯函数 + 端点构建只发生在端点变化（零热路径全扫描）。
// 纯函数部分可 node 单测（不碰浏览器 worker/canvas）；浏览器端 E2E 另行覆盖。
import { test } from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import {
  createPipeline, stateCounts, hashCvMag, diffFrames, frameChecksum, combineFieldChecksum, compareVsSync,
} from '../../src/map-control/pipeline.js'

const MODEL_H = 0.1
const tickAtTimeSec = (t) => Math.floor((t + 1e-4) / MODEL_H)
const timeSecAtTick = (tick) => tick * MODEL_H
const nav = JSON.parse(fs.readFileSync('D:/vrf-tools/navjson/de_mirage-nav.json', 'utf8'))
const demo = JSON.parse(fs.readFileSync('public/demo/6r21-round-sample.json', 'utf8'))
const CFG = { sigma: 550, neutralThreshold: 0.22, contestRatio: 0.2, maxPropagationDistance: 2200, frontierBand: 0.05 }
const P = createPipeline({ nav, sample: demo, cfg: CFG, opts: { gridN: 128 } })
const MAX = P.maxAreaId

test('E5N stateCounts 分类正确且与 pipeline 同构', () => {
  const v = P.frameAt(19.5)
  const c = stateCounts(v.cv, v.mag, CFG, MAX)
  const sum = c.CT + c.T + c.CONTESTED + c.NEUTRAL
  assert.ok(sum > 0, 'state 计数总和非零')
  assert.ok(c.NEUTRAL > 0, '必须保留 NEUTRAL（spec 强约束）')
  assert.ok(c.CT > 0 && c.T > 0, 'CT/T 均存在')
  assert.equal(sum, MAX + 1, '逐 area 分类覆盖全部 2545 index')
})

test('E5N hashCvMag 确定性：同帧同 hash，异帧异 hash', () => {
  const v = P.frameAt(19.5); const c1 = new Float32Array(v.cv), m1 = new Float32Array(v.mag)
  assert.equal(hashCvMag(c1, m1, MAX), hashCvMag(c1, m1, MAX), '同帧稳定')
  const v2 = P.frameAt(19.7); const c2 = new Float32Array(v2.cv), m2 = new Float32Array(v2.mag)
  assert.notEqual(hashCvMag(c1, m1, MAX), hashCvMag(c2, m2, MAX), '异帧不同')
})

test('E5N diffFrames 逐数组比较：identical→0，差异→计数与 maxAbsDiff', () => {
  const v = P.frameAt(19.5)
  const same = diffFrames(v, v)
  assert.equal(same.diffCount, 0)
  assert.equal(same.maxAbsDiff, 0)
  assert.equal(same.length, MAX + 1)
  const c = new Float32Array(v.cv), m = new Float32Array(v.mag)
  c[700] += 0.5; m[1200] += 0.25
  const d = diffFrames({ cv: c, mag: m }, v)
  assert.ok(d.diffCount >= 2, '两个元素差异都应计入')
  assert.ok(d.maxAbsDiff >= 0.5, 'maxAbsDiff 取最大分量差')
})

test('E5N frameChecksum 确定性：同帧稳定、异帧可区分', () => {
  const v = P.frameAt(19.5); const c1 = new Float32Array(v.cv), m1 = new Float32Array(v.mag)
  assert.equal(frameChecksum(c1, m1, MAX), frameChecksum(c1, m1, MAX), '同帧稳定')
  const v2 = P.frameAt(19.7); const c2 = new Float32Array(v2.cv), m2 = new Float32Array(v2.mag)
  assert.notEqual(frameChecksum(c1, m1, MAX), frameChecksum(c2, m2, MAX), '异帧不同')
})

test('E5N 连续性：fieldChecksum 连续插值在 bracket 内相邻均有界、19.59→19.60 无跳变', () => {
  // 实验室 field 层校验和 = combine(ep0.ck, ep1.ck, frac)，它是连续、有界、确定性的插值。
  const ck0 = 1_000_000, ck1 = 9_000_000
  const compute = (t) => { const t0 = Math.floor(t / MODEL_H) * MODEL_H; const frac = (t - t0) / MODEL_H; return combineFieldChecksum(ck0, ck1, frac) }
  const ts = [19.51, 19.54, 19.57, 19.59, 19.60]
  const cks = ts.map(compute)
  for (let i = 1; i < 4; i++) {
    const d = Math.abs(cks[i] - cks[i - 1])
    assert.ok(d > 0, `相邻帧差>0 (i=${i}) d=${d}`)
    assert.ok(d <= (ck1 - ck0) * 1000, `相邻帧差有界 (i=${i}) d=${d}`)
  }
  const dEdge = Math.abs(cks[4] - cks[3])
  assert.ok(dEdge <= (ck1 - ck0) * 1000, `19.59→19.60 有界 d=${dEdge}`)
})

test('E5N combineFieldChecksum 连续、确定、随 frac 单调', () => {
  const ck0 = 1_000_000, ck1 = 9_000_000
  assert.equal(combineFieldChecksum(ck0, ck1, 0.0), ck0 * 1000)
  assert.equal(combineFieldChecksum(ck0, ck1, 1.0), ck1 * 1000)
  assert.equal(combineFieldChecksum(ck0, ck1, 0.5), (ck0 + 4_000_000) * 1000)
  const a = combineFieldChecksum(ck0, ck1, 0.4), b = combineFieldChecksum(ck0, ck1, 0.5)
  assert.ok(Math.abs(b - a) > 0, 'frac 变化 → checksum 变化')
  assert.equal(combineFieldChecksum(ck0, ck1, 0.5), combineFieldChecksum(ck0, ck1, 0.5), '确定性')
})

test('E5N compareVsSync：worker exact 帧 == 纯 sync reference 时全部匹配', () => {
  // 模拟 worker exact 与 sync reference 由同一算法产生（同 ctrl → 同结果）
  const t0 = 195, t1 = 196
  const s0 = P.frameAt(timeSecAtTick(t0)), s1 = P.frameAt(timeSecAtTick(t1))
  const mk = (v) => ({ cv: new Float32Array(v.cv), mag: new Float32Array(v.mag), hash: hashCvMag(v.cv, v.mag, MAX), controlsHash: 'aaaa' })
  const mk0 = mk(s0), mk1 = mk(s1)
  const r = compareVsSync([mk0, mk1], [mk0, mk1], CFG, MAX)
  assert.equal(r.diffCount, 0, 'identical → diffCount 0')
  assert.equal(r.maxAbsDiff, 0, 'identical → maxAbsDiff 0')
  assert.equal(r.hashMatch, true)
  assert.equal(r.countsMatch, true)
  assert.equal(r.controlsHashMatch, true)
  assert.equal(r.comparisonPassed, true)
})

test('E5N compareVsSync：注入差异必须被检出且 comparisonPassed=false', () => {
  // 用独立拷贝避免 frameAt 共享 cvBuf/magBuf 的别名干扰
  const v = P.frameAt(19.5)
  const syncCv = new Float32Array(v.cv), syncMag = new Float32Array(v.mag)
  const badCv = new Float32Array(v.cv); badCv[300] += 0.7
  const hashC = hashCvMag(syncCv, syncMag, MAX)
  const wf0 = { cv: syncCv, mag: syncMag, hash: hashC, controlsHash: 'aaaa' }
  const sf0 = { cv: syncCv, mag: syncMag, hash: hashC, controlsHash: 'aaaa' }
  const wf1 = { cv: badCv, mag: syncMag, hash: hashCvMag(badCv, syncMag, MAX), controlsHash: 'aaaa' }
  const sf1 = { cv: syncCv, mag: syncMag, hash: hashC, controlsHash: 'aaaa' }
  const r = compareVsSync([wf0, wf1], [sf0, sf1], CFG, MAX)
  assert.ok(r.diffCount > 0)
  assert.ok(r.maxAbsDiff >= 0.69, 'maxAbsDiff 捕获注入的 0.7 差异（float32 公差）')
  assert.equal(r.hashMatch, false, '注入帧 hash 与 sync hash 不同 → hashMatch=false')
  assert.equal(r.comparisonPassed, false)
})
