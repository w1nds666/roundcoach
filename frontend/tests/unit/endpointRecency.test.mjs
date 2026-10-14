import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(new URL('../../src/map-control/mapControlEngine.js', import.meta.url), 'utf8')
const handler = source.slice(source.indexOf('  function ensureEndpoint('), source.indexOf('  // 提前构建下一 bracket'))
test('reusing an old endpoint protects its bitmap from the next eviction', () => {
  const endpointCache = new Map(Array.from({ length: 6 }, (_, tick) => [tick, { hash: tick, cfgHash: 'cfg', gen: 1, fieldCanvas: { width: 128, height: 128 } }]))
  const ctx = { endpointCache, generation: 1, frameKey: t => t, resolveFrame: t => ({ hash: t, cfgHash: 'cfg' }) }
  vm.createContext(ctx)
  vm.runInContext(handler, ctx)
  const reused = vm.runInContext('ensureEndpoint(0)', ctx)
  endpointCache.set(6, {})
  const oldest = endpointCache.keys().next().value
  endpointCache.get(oldest).fieldCanvas.width = 0
  endpointCache.delete(oldest)
  assert.equal(reused.fieldCanvas.width, 128)
  assert.equal(endpointCache.size, 6)
  assert.equal(oldest, 1)
})
