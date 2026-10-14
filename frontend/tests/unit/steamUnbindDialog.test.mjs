import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../../src/components/SteamBind.vue', import.meta.url), 'utf8')
function close(pending) {
  const ctx = { unbinding: { value: pending }, dialogOpen: { value: true }, password: { value: 'synthetic-only' } }
  vm.createContext(ctx)
  vm.runInContext(source.slice(source.indexOf('function closeUnbind'), source.indexOf('function unbindError')) + '\ncloseUnbind()', ctx)
  return ctx
}
test('pending unbind cannot dismiss the dialog through its backdrop', () => {
  const ctx = close(true)
  assert.equal(ctx.dialogOpen.value, true)
  assert.equal(ctx.password.value, 'synthetic-only')
})
test('idle cancellation closes the dialog and clears its password', () => {
  const ctx = close(false)
  assert.equal(ctx.dialogOpen.value, false)
  assert.equal(ctx.password.value, '')
})
