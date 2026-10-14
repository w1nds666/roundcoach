import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../../src/components/PersonalCombatMap.vue', import.meta.url), 'utf8')

test('returning from density schedules drawing on the newly mounted point canvas', () => {
  const watches = [], ticks = [], mode = { value: 'density' }, canvasRef = { value: null }
  let draws = 0
  const ctx = { mode, canvasRef, radarImg: {}, combat: {}, props: {},
    nextTick: fn => ticks.push(fn), render: () => { draws++ },
    watch: (deps, fn) => watches.push({ deps, fn }) }
  vm.createContext(ctx)
  vm.runInContext(source.slice(source.indexOf('let renderPending ='), source.indexOf('onMounted(')), ctx)
  mode.value = 'points'
  for (const w of watches) {
    if (w.deps === mode || (Array.isArray(w.deps) && w.deps.includes(mode))) w.fn()
  }
  assert.ok(ticks.length > 0, 'mode change must schedule a post-mount render')
  canvasRef.value = {} // Vue recreates the canvas before nextTick.
  while (ticks.length) ticks.shift()()
  assert.equal(draws, 1)
})
