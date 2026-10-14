import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../../src/views/Upload.vue', import.meta.url), 'utf8')
function setup(get) {
  let callback, active = false
  const ctx = { api: { get }, demoId: { value: 19 }, phase: { value: 'queued' }, statusText: { value: '' }, error: { value: '' },
    setInterval: fn => { callback = fn; active = true; return 1 }, clearInterval: () => { active = false } }
  vm.createContext(ctx)
  vm.runInContext(source.slice(source.indexOf('let timer = null'), source.indexOf('onBeforeUnmount(')), ctx)
  return { ctx, run: s => vm.runInContext(s, ctx), tick: () => callback(), active: () => active }
}
test('successful pending responses stop at the declared 60-request limit', async () => {
  let calls = 0
  const s = setup(async () => { calls++; return { status: 'parsing' } })
  s.run('startPolling()')
  for (let i = 0; i < 60; i++) await s.tick()
  assert.equal(calls, 60)
  assert.equal(s.active(), false)
  assert.equal(s.ctx.phase.value, 'queued')
  assert.match(s.ctx.statusText.value, /我的复盘/)
})
test('slow requests do not overlap, and stopped requests cannot change state', async () => {
  let resolve, calls = 0
  const s = setup(() => { calls++; return new Promise(r => { resolve = r }) })
  s.run('startPolling()')
  const pending = s.tick()
  const duplicate = s.tick()
  assert.equal(calls, 1)
  s.run('stopPolling()')
  resolve({ status: 'parsed' }); await pending; await duplicate
  assert.equal(s.ctx.phase.value, 'queued')
})
test('terminal status still finishes immediately', async () => {
  const s = setup(async () => ({ status: 'parsed' }))
  s.run('startPolling()'); await s.tick()
  assert.equal(s.ctx.phase.value, 'parsed'); assert.equal(s.active(), false)
})

test('uploaded status stays queued until the server reports parsing', async () => {
  let status = 'uploaded'
  const s = setup(async () => ({ status }))
  s.run('startPolling()'); await s.tick()
  assert.equal(s.ctx.phase.value, 'queued')
  assert.match(s.ctx.statusText.value, /队列/)
  assert.equal(s.active(), true)
  status = 'parsing'; await s.tick()
  assert.equal(s.ctx.phase.value, 'parsing')
  assert.equal(s.ctx.statusText.value, '解析中…')
})
