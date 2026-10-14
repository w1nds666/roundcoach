import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

// Exercise the actual component handler without mounting the graphics engine.
const source = readFileSync(new URL('../../src/components/MapControlReplay.vue', import.meta.url), 'utf8')
const handler = source.slice(source.indexOf('function togglePlay()'), source.indexOf('function seek('))
function state(time, reducedMotion) {
  const ctx = { currentTime: { value: time }, duration: { value: 40 }, reduced: { value: reducedMotion }, playing: { value: false }, lastFrame: 0, rafId: 0,
    renders: 0, scheduled: 0, scheduleRender() { ctx.renders++ }, requestAnimationFrame() { ctx.scheduled++; return 1 }, cancelAnimationFrame() {}, animate() {} }
  vm.createContext(ctx)
  vm.runInContext(handler, ctx)
  return ctx
}
test('reduced motion toggles both endpoints without continuous playback', () => {
  const ctx = state(0, true)
  vm.runInContext('togglePlay()', ctx)
  assert.equal(ctx.currentTime.value, 40)
  vm.runInContext('togglePlay()', ctx)
  assert.equal(ctx.currentTime.value, 0)
  assert.equal(ctx.playing.value, false)
  assert.equal(ctx.scheduled, 0)
  assert.equal(ctx.renders, 2)
})
test('ordinary playback restarts from end and schedules animation', () => {
  const ctx = state(40, false)
  vm.runInContext('togglePlay()', ctx)
  assert.equal(ctx.currentTime.value, 0)
  assert.equal(ctx.playing.value, true)
  assert.equal(ctx.scheduled, 1)
})
