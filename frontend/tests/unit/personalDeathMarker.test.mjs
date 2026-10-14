import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../../src/components/PersonalTacticalReplay.vue', import.meta.url), 'utf8')
function state(tick, pts, result = 'dead') {
  let crosses = 0
  const ctx = { pts, cTick: tick, outcome: { value: result },
    ctx: { save() {}, restore() {} }, COL: { death: '#f85149' }, drawX: () => crosses++ }
  vm.createContext(ctx)
  vm.runInContext(src.slice(src.indexOf('    // 死亡 X'), src.indexOf('    if (st.px != null && !currentGone)')) + '\nglobalThis.gone = currentGone', ctx)
  return { crosses, gone: ctx.gone }
}

test('death marker and player removal share the same tick, including backward seek', () => {
  const pts = [{ tick: 10, alive: true }, { tick: 20, alive: false }]
  for (const tick of [19.99, 20, 40, 19.99]) {
    assert.deepEqual(state(tick, pts), { crosses: tick >= 20 ? 1 : 0, gone: tick >= 20 })
  }
})
test('final dead outcome alone must not invent a death position or time', () => {
  assert.deepEqual(state(40, [{ tick: 10, alive: true }]), { crosses: 0, gone: false })
})
test('death glyph draws two crossing diagonal strokes at the authoritative position', () => {
  const calls = []
  const ctx = { beginPath() {}, moveTo: (...p) => calls.push(['M', ...p]), lineTo: (...p) => calls.push(['L', ...p]), stroke() {} }
  const scope = { SIZE: 720, ctx }
  vm.createContext(scope)
  vm.runInContext(src.slice(src.indexOf('function drawX('), src.indexOf('function drawPath(')) + '\ndrawX(ctx, .5, .25, 7, "#f85149")', scope)
  assert.deepEqual(calls, [['M', 353, 173], ['L', 367, 187], ['M', 367, 173], ['L', 353, 187]])
  assert.equal(ctx.strokeStyle, '#f85149')
  assert.equal(ctx.lineWidth, 3)
})
