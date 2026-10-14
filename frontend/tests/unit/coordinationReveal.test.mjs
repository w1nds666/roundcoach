import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../../src/components/CoordinationInsight.vue', import.meta.url), 'utf8')
function setup(count = 5, reduced = false) {
  let cleanup, callback, active = false, resolveRequest
  const ctx = { ref: value => ({ value }), data: { value: { conclusions: Array(count), rounds: Array(100) } },
    window: { matchMedia: () => ({ matches: reduced }) }, onBeforeUnmount: fn => { cleanup = fn },
    setInterval: fn => { active = true; callback = fn; return 1 }, clearInterval: () => { active = false },
    loading: { value: false }, error: { value: '' }, props: { demoId: 21 }, ApiError: Error,
    api: { get: () => new Promise(resolve => { resolveRequest = resolve }) } }
  vm.createContext(ctx)
  vm.runInContext(source.slice(source.indexOf('const revealCount ='), source.indexOf('const METRIC_LABEL =')), ctx)
  vm.runInContext(source.slice(source.indexOf('async function load()'), source.indexOf('onMounted(load)')), ctx)
  return { ctx, run: s => vm.runInContext(s, ctx), tick: () => callback(), active: () => active, cleanup: () => cleanup(), resolve: x => resolveRequest(x) }
}
test('only visible conclusions determine callback count', () => {
  const s = setup(); s.run('revealProgressively()')
  for (let i = 0; i < 5; i++) s.tick()
  assert.equal(s.active(), false)
  assert.equal(s.run('revealCount.value'), 5)
})
test('empty and reduced-motion lists do not start intervals', () => {
  for (const [n, reduced] of [[0, false], [5, true]]) {
    const s = setup(n, reduced); s.run('revealProgressively()')
    assert.equal(s.active(), false); assert.equal(s.run('revealCount.value'), n)
  }
})
test('unmount cancels scheduling and ignores a late successful response', async () => {
  const s = setup(); s.run('revealProgressively()'); const previous = s.ctx.data.value
  const pending = s.run('load()'); s.cleanup(); s.resolve({ conclusions: [1] }); await pending
  assert.equal(s.active(), false); assert.equal(s.ctx.data.value, previous)
  s.run('revealProgressively()'); assert.equal(s.active(), false)
})
