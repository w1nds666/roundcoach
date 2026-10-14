import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../../src/views/Login.vue', import.meta.url), 'utf8')
function setup() {
  let resolve, reject, calls = 0
  const ctx = { mode: { value: 'login' }, submitting: { value: false }, error: { value: '' }, passwordError: { value: '' },
    email: { value: 'test@example.com' }, password: { value: 'password123' }, nickname: { value: 'Test' },
    validatePassword: () => '', ApiError: Error, route: { query: {} }, router: { push() {} },
    authStore: { login: () => { calls++; return new Promise((r, j) => { resolve = r; reject = j }) } } }
  vm.createContext(ctx)
  vm.runInContext(source.slice(source.indexOf('function switchMode('), source.indexOf('</script>')), ctx)
  return { ctx, run: s => vm.runInContext(s, ctx), finish: () => resolve(), fail: () => reject(new Error('登录失败')), calls: () => calls }
}
test('pending authentication cannot submit twice or change mode; completion unlocks mode', async () => {
  const s = setup(), pending = s.run('submit()')
  s.run('switchMode("register")')
  assert.equal(s.ctx.mode.value, 'login')
  await s.run('submit()')
  assert.equal(s.calls(), 1)
  s.finish(); await pending
  assert.equal(s.ctx.submitting.value, false)
  s.run('switchMode("register")')
  assert.equal(s.ctx.mode.value, 'register')
})
test('failed authentication restores retry and mode switching without clearing entered email', async () => {
  const s = setup(), pending = s.run('submit()')
  s.fail(); await pending
  assert.equal(s.ctx.submitting.value, false)
  assert.equal(s.ctx.error.value, '登录失败')
  assert.equal(s.ctx.email.value, 'test@example.com')
  const retry = s.run('submit()')
  assert.equal(s.calls(), 2)
  s.finish(); await retry
  s.run('switchMode("register")')
  assert.equal(s.ctx.error.value, '')
  assert.equal(s.ctx.mode.value, 'register')
})
