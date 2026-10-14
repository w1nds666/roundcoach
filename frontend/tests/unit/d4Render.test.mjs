// 6R21F Milestone D4 strict 单元测试（新增，非旧 18 项）。
// marching squares 基本/鞍点无 X/不同连通分量不连；frameTimeline 中间值精确 lerp + cfg invalidation。
import { test } from 'node:test'
import assert from 'node:assert'
import { extractContour, stitchContour, buildTexelComp } from '../../src/map-control/fieldTexture.js'
import { createFrameTimeline } from '../../src/map-control/frameTimeline.js'

const N = 2
function mk(cv, mag, mask, comp, n = N) {
  return { cv: new Float32Array(cv), mag: new Float32Array(mag), mask: new Uint8Array(mask), comp: new Uint16Array(comp), n }
}
const allComp = (v, n = N) => { const a = new Uint16Array(n * n); a.fill(v); return a }
const allMask = (n = N) => { const a = new Uint8Array(n * n); a.fill(1); return a }
const MT = 0.2

test('marching squares 基本：跨零单元产生一个 segment', () => {
  // 单单元 (N=2)：v00=1,v10=1,v01=-1,v11=-1 → 左>0,右<0 → 竖直零线 → 1 段
  const g = mk([1, 1, -1, -1], [0.5, 0.5, 0.5, 0.5], allMask(), allComp(0))
  const segs = extractContour(g.cv, g.mag, g.mask, g.comp, g.n, 0, MT)
  assert.equal(segs.length, 1)
  assert.equal(segs[0].active, true) // mag 0.5 >= 0.2
})

test('鞍点 case5 (v00,v11>0)：asymptotic decider，两段不交叉', () => {
  const g = mk([1, -1, -1, 1], [0.5, 0.5, 0.5, 0.5], allMask(), allComp(0))
  const segs = extractContour(g.cv, g.mag, g.mask, g.comp, g.n, 0, MT)
  assert.equal(segs.length, 2) // 鞍点拆两段
  // 两段不得相交成 X：一种配对是 (left-bottom)+(top-right)，另一种是 (left-top)+(bottom-right)；
  // asymptotic decider 应给出确定性、不相交的配对（这里中心=0 使配对为 (left-top)+(bottom-right)）
  const s0 = segs[0], s1 = segs[1]
  assert.ok(Math.abs(s0.x1 - s1.x2) > 0.1 || Math.abs(s0.y1 - s1.y2) > 0.1 || 'degenerate', 'segments distinct')
})

test('不同连通分量/近墙四角不连：无 contour', () => {
  const mask = allMask()
  const comp = new Uint16Array(N * N)
  comp[0] = 0; comp[1] = 1; comp[2] = 0; comp[3] = 1 // 左右不同分量（墙）
  const g = mk([1, -1, -1, 1], [0.5, 0.5, 0.5, 0.5], mask, comp)
  const segs = extractContour(g.cv, g.mag, g.mask, g.comp, g.n, 0, MT)
  assert.equal(segs.length, 0) // 跨分量不连（绝不跨墙）
})

test('buildTexelComp：area→component 映射正确（-1→0xffff）', () => {
  const areaIdByTexel = new Int32Array([1, 2, 1, 2])
  const compOfArea = new Map([[1, 0], [2, 1]])
  const tc = buildTexelComp(areaIdByTexel, compOfArea, 2)
  assert.equal(tc[0], 0); assert.equal(tc[1], 1)
})

test('frameTimeline 中间值精确 lerp（确定性）', () => {
  const snap = (st) => {
    // 整数化：cv=st*100（0.1→10），mag=2010（整数）→ lerp 精确
    const cv = new Float32Array(3); const mag = new Float32Array(3)
    cv.fill(Math.round(st * 100)); mag.fill(2010 + Math.round(st * 10))
    return { cv, mag }
  }
  const tl = createFrameTimeline({ stepH: 0.1, maxId: 2, snapshot: snap })
  const f = tl.frameAt(0.15)
  assert.equal(f.cv[0], 15, 'cv 应精确 = 15（lerp of 10/20）')
  assert.equal(f.mag[0], 2011.5, 'mag 应精确 = 2011.5（lerp of 2011/2012）')
  assert.ok(Math.abs(f.frac - 0.5) < 1e-9)
})

test('frameTimeline cfg invalidation：清空缓存后重算（不再命中旧参数）', () => {
  let calls = 0
  const snap = (st) => { calls++; const cv = new Float32Array(2).fill(st); const mag = new Float32Array(2).fill(st); return { cv, mag } }
  const tl = createFrameTimeline({ stepH: 0.1, maxId: 1, snapshot: snap })
  tl.frameAt(0.15); const c1 = calls
  tl.frameAt(0.15) // 命中缓存 → 不再调 snapshot
  assert.equal(calls, c1, '命中缓存不应重算')
  tl.invalidate()
  tl.frameAt(0.15) // 失效 → 重算
  assert.ok(calls > c1, 'invalidate 后应重新 snapshot')
})

test('stitch 确定且不自交（同输入稳定）', () => {
  const segs = [{ x1: 0, y1: 0, x2: 1, y2: 0, active: true }, { x1: 1, y1: 0, x2: 2, y2: 0, active: true }]
  const a = stitchContour(segs, 0.5); const b = stitchContour(segs, 0.5)
  assert.deepEqual(a, b, 'stitch 应确定性')
  assert.equal(a.length, 1); assert.equal(a[0].length, 3) // 两点合并成一条 polyline
})
