import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../../src/views/Upload.vue', import.meta.url), 'utf8')
function setup() {
  let cleanup, resolve, progress, timers = 0
  const pending = new Promise(r => { resolve = r })
  const ctx = { ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
    useRouter: () => ({}), onBeforeUnmount: fn => { cleanup = fn }, ApiError: Error,
    api: { upload: (_url, _file, cb) => { progress = cb; return pending }, uploadBatch: () => pending },
    setInterval: () => { timers++; return 1 }, clearInterval() {} }
  vm.createContext(ctx)
  vm.runInContext(source.split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, ''), ctx)
  return { run: s => vm.runInContext(s, ctx), cleanup: () => cleanup(), resolve, progress: p => progress(p), timers: () => timers }
}
test('late upload completion and progress cannot restart an unmounted page', async () => {
  const s = setup(); s.run('file.value = { name: "fixture.dem", size: 8 }')
  const pending = s.run('startUpload()'); s.cleanup(); s.progress(100)
  s.resolve({ demo_id: 19 }); await pending
  assert.equal(s.timers(), 0)
  assert.equal(s.run('demoId.value'), null)
  assert.equal(s.run('progress.value'), 0)
})
test('late batch completion does not write results into a disposed page', async () => {
  const s = setup(); s.run('batchFiles.value = [{ name: "fixture.dem" }]')
  const pending = s.run('startBatch()'); s.cleanup(); s.resolve({ results: [1] }); await pending
  assert.equal(s.run('batchResults.value'), null)
})
