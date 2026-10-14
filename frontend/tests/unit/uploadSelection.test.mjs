import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'
const src = readFileSync(new URL('../../src/views/Upload.vue', import.meta.url), 'utf8')
function setup(allowed) {
  let clicks = 0, picks = 0
  const ctx = { canPick: { value: allowed }, fileInput: { value: { click: () => clicks++ } },
    pick: () => picks++, file: { value: 'original' }, phase: { value: 'uploading' }, error: { value: '' }, batchResults: { value: null }, batchFiles: { value: [] } }
  vm.createContext(ctx)
  vm.runInContext(src.slice(src.indexOf('function onSelect('), src.indexOf('async function startBatch(')), ctx)
  return { ctx, run: s => vm.runInContext(s, ctx), clicks: () => clicks, picks: () => picks }
}
test('locked uploader rejects keyboard and late selection events', () => {
  const s = setup(false)
  s.run('onDropKey({ key: "Enter", preventDefault() {} })')
  s.run('onSelect({ target: { files: [{ name: "late.dem" }], value: "late" } })')
  assert.equal(s.clicks(), 0); assert.equal(s.picks(), 0)
  s.run('selectBatch([{name:"a.dem"}, {name:"b.dem"}])')
  assert.equal(s.ctx.file.value, 'original')
  assert.equal(s.ctx.phase.value, 'uploading')
})
test('idle uploader retains keyboard and single file selection', () => {
  const s = setup(true)
  s.run('onDropKey({ key: " ", preventDefault() {} })')
  s.run('onSelect({ target: { files: [{ name: "ok.dem" }], value: "ok" } })')
  assert.equal(s.clicks(), 1); assert.equal(s.picks(), 1)
})
