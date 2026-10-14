// 6R21F-E6D：地图接入目录（demo→地图→NavMesh/radar/sample URL）红测。
// 缺失行为：产品需按权威 mapName 选择 backend /api/maps/{map}/navmesh 与对应 transform；
// 当前无任何 map→URL 目录模块（MapControlReplay/Report 硬编码 de_mirage）。
import { test } from 'node:test'
import assert from 'node:assert'
import {
  replayConfigFor,
  isReplayMap,
  authoritativeMapForDemo,
  demoMapAlignment,
} from '../../src/map-control/mapCatalog.js'

test('E6D 目录：de_inferno 存在且 URL/名字来自权威 mapName', () => {
  assert.equal(isReplayMap('de_inferno'), true)
  const c = replayConfigFor('de_inferno')
  assert.ok(c, 'de_inferno 必须在接入目录内')
  assert.equal(c.navUrl, '/api/maps/de_inferno/navmesh')
  assert.equal(c.radarUrl, '/api/maps/de_inferno/radar')
  assert.equal(c.sampleUrl, '/demo/6r22-round-sample.json')
})

test('E6D 目录：de_mirage 仍保留（E6A 回归不破坏）', () => {
  assert.equal(isReplayMap('de_mirage'), true)
  const c = replayConfigFor('de_mirage')
  assert.equal(c.navUrl, '/api/maps/de_mirage/navmesh')
  assert.equal(c.radarUrl, '/api/maps/de_mirage/radar')
  assert.equal(c.sampleUrl, '/demo/6r21-round-sample.json')
})

test('E6E 目录：de_dust2 已接入（demo21 权威地图）', () => {
  assert.equal(isReplayMap('de_dust2'), true)
  const c = replayConfigFor('de_dust2')
  assert.ok(c, 'de_dust2 必须在接入目录内')
  assert.equal(c.navUrl, '/api/maps/de_dust2/navmesh')
  assert.equal(c.radarUrl, '/api/maps/de_dust2/radar')
  assert.equal(c.sampleUrl, '/demo/dust2-round-sample.json')
})

test('E6D 目录：demo→地图权威对齐（demo19→de_mirage、demo21→de_dust2、demo22→de_inferno）', () => {
  assert.ok(demoMapAlignment, '必须暴露 demo→地图 权威对齐')
  assert.equal(authoritativeMapForDemo('19'), 'de_mirage')
  assert.equal(authoritativeMapForDemo('21'), 'de_dust2')
  assert.equal(authoritativeMapForDemo('22'), 'de_inferno')
})

test('E6D 目录：未接入地图与未知 demo 不得误用 / 硬编码 Mirage', () => {
  assert.equal(isReplayMap('de_anubis'), false)
  assert.equal(isReplayMap('de_ancient'), false)
  assert.equal(isReplayMap('de_vertigo'), false)
  assert.equal(replayConfigFor('de_anubis'), null)
  assert.equal(authoritativeMapForDemo('20'), null)
  // de_dust2 / de_inferno 目录绝不指向 mirage 资源（无 Mirage 硬编码）
  for (const m of ['de_dust2', 'de_inferno']) {
    const c = replayConfigFor(m)
    assert.ok(!/mirage/.test(c.navUrl) && !/mirage/.test(c.radarUrl) && !/mirage/.test(c.sampleUrl))
  }
})
