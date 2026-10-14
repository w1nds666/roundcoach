// 6R21E 纯空间控制模型 synthetic 红绿测试（node --test）。
// 覆盖 spec §29：直走廊 / 墙隔断 / 瓶颈 / 远端 neutral / 死亡玩家零贡献 / 相同输入 hash 稳定 /
// 关键：屏幕很近但图无边连接时绝不跨墙传播。
import { test } from 'node:test'
import assert from 'node:assert'
import {
  createGraph, addNode, addEdge, multiSourceDijkstra, influence,
  computeControlFrame, mapPointToArea, frameHash,
} from '../../src/map-control/model.js'

// 一条链 CT...T：nodes(id,x,y)，edges 相邻
function chain(ids, xs) {
  const g = createGraph()
  ids.forEach((id, i) => addNode(g, id, xs[i], 0))
  for (let i = 0; i < ids.length - 1; i++) addEdge(g, ids[i], ids[i + 1])
  return g
}
const CONF = { sigma: 15, neutralThreshold: 0.05, contestRatio: 0.35, maxPropagationDistance: null }
const P = (team, areaId, alive = true) => ({ team, areaId, alive })

test('直走廊：CT 左强右弱、T 右强左弱、中部 contested', () => {
  const ids = ['A', 'B', 'C', 'D', 'E', 'F']
  const g = chain(ids, [0, 10, 20, 30, 40, 50])
  const fr = computeControlFrame(g, [P('CT', 'A'), P('T', 'F')], CONF)
  const byId = Object.fromEntries(fr.areas.map((a) => [a.areaId, a]))
  assert.equal(byId['A'].state, 'CT')
  assert.equal(byId['F'].state, 'T')
  // 方向性衰减
  assert.ok(byId['A'].ctInfluence > byId['B'].ctInfluence)
  assert.ok(byId['B'].ctInfluence > byId['C'].ctInfluence)
  assert.ok(byId['F'].tInfluence > byId['E'].tInfluence)
  assert.ok(byId['E'].tInfluence > byId['D'].tInfluence)
  // 中部存在 contested（非一刀切 CT/T）
  assert.ok(fr.areas.some((a) => a.state === 'CONTESTED'), '走廊中部应出现 contested')
})

test('墙隔断：B↔C 无边 → CT 不得出现在 C/D（屏幕近也不跨墙）', () => {
  const g = createGraph()
  // CT 侧 A-B；墙另一侧 C-D；B 与 C 物理很近但**无边连接**
  addNode(g, 'A', 0, 0); addNode(g, 'B', 10, 0)
  addNode(g, 'C', 12, 0); addNode(g, 'D', 22, 0)
  addEdge(g, 'A', 'B'); addEdge(g, 'C', 'D') // 不连 B-C
  const fr = computeControlFrame(g, [P('CT', 'A')], CONF)
  const byId = Object.fromEntries(fr.areas.map((a) => [a.areaId, a]))
  assert.equal(byId['A'].state, 'CT')
  assert.equal(byId['B'].state, 'CT')
  // 关键：C/D 与 A 屏幕很近，但图无边 → 必须 NEUTRAL（绝不跨墙传播）
  assert.equal(byId['C'].state, 'NEUTRAL', 'C 不应跨墙获得 CT 控制')
  assert.equal(byId['D'].state, 'NEUTRAL', 'D 不应跨墙获得 CT 控制')
})

test('瓶颈：控制经 CHOKE 传播而非径向填满近旁', () => {
  const g = createGraph()
  //        A
  //        |
  // CT - CHOKE - B - C
  //        |
  //        D
  addNode(g, 'CT', 0, 0); addNode(g, 'CHOKE', 10, 0)
  addNode(g, 'B', 20, 0); addNode(g, 'C', 30, 0)
  addNode(g, 'A', 10, -10); addNode(g, 'D', 10, 10)
  addEdge(g, 'CT', 'CHOKE'); addEdge(g, 'CHOKE', 'B'); addEdge(g, 'B', 'C')
  addEdge(g, 'CHOKE', 'A'); addEdge(g, 'CHOKE', 'D')
  const fr = computeControlFrame(g, [P('CT', 'CT')], CONF)
  const byId = Object.fromEntries(fr.areas.map((a) => [a.areaId, a]))
  // 经 CHOKE 才能到达 B/C/A/D
  assert.equal(byId['CHOKE'].state, 'CT')
  assert.equal(byId['B'].state, 'CT')
  assert.equal(byId['C'].state, 'CT')
  assert.equal(byId['A'].state, 'CT')
  // 距 CT 的图距离：C(30) > B(20) > CHOKE(10) → influence 递减（拓扑约束，非径向）
  assert.ok(byId['CHOKE'].ctInfluence > byId['B'].ctInfluence)
  assert.ok(byId['B'].ctInfluence > byId['C'].ctInfluence)
})

test('远端 neutral：受限传播距离 → 远端区域保持 NEUTRAL', () => {
  const ids = ['A', 'B', 'C', 'D', 'E', 'F']
  const g = chain(ids, [0, 10, 20, 30, 40, 50])
  const cfg = { sigma: 15, neutralThreshold: 0.05, contestRatio: 0.35, maxPropagationDistance: 35 }
  const fr = computeControlFrame(g, [P('CT', 'A')], cfg)
  const byId = Object.fromEntries(fr.areas.map((a) => [a.areaId, a]))
  assert.equal(byId['A'].state, 'CT')
  assert.equal(byId['B'].state, 'CT')
  // 远端 E/F 超出传播距离 → NEUTRAL（地图不整个变 CT）
  assert.equal(byId['E'].state, 'NEUTRAL')
  assert.equal(byId['F'].state, 'NEUTRAL')
})

test('死亡玩家零贡献：dead 玩家不影响控制帧', () => {
  const ids = ['A', 'B', 'C']
  const g = chain(ids, [0, 10, 20])
  const base = computeControlFrame(g, [P('CT', 'A')], CONF)
  // 增加一个 A 的**死亡**队友在 C → 不该改变任何影响
  const withDead = computeControlFrame(g, [P('CT', 'A'), P('CT', 'C', false)], CONF)
  assert.equal(base.hash, withDead.hash, 'dead 玩家不得改变控制帧')
  const byId = Object.fromEntries(withDead.areas.map((a) => [a.areaId, a]))
  // C 在仅 CT@A 下是 CT（可达）——死亡队友不带来额外来源
  assert.ok(byId['C'].ctInfluence < byId['B'].ctInfluence)
  // 只给 dead 来源时（无存活）→ 全 neutral（来源空）
  const onlyDead = computeControlFrame(g, [P('CT', 'C', false)], CONF)
  assert.ok(onlyDead.areas.every((a) => a.state === 'NEUTRAL'), '仅有 dead 玩家 → 全程 NEUTRAL')
})

test('相同输入 hash 稳定（确定性）', () => {
  const ids = ['A', 'B', 'C', 'D']
  const g = chain(ids, [0, 10, 20, 30])
  const a = computeControlFrame(g, [P('CT', 'A'), P('T', 'D')], CONF)
  const b = computeControlFrame(g, [P('CT', 'A'), P('T', 'D')], CONF)
  assert.equal(a.hash, b.hash)
  assert.equal(a.hash, frameHash(a.areas))
})

test('绝不跨墙传播（硬断言）：屏幕相近但图无边连接 → 零 influence', () => {
  const g = createGraph()
  addNode(g, 'CT', 0, 0)
  addNode(g, 'nearWall', 2, 1)  // 屏幕距离很近
  const fr = computeControlFrame(g, [P('CT', 'CT')], CONF)
  const byId = Object.fromEntries(fr.areas.map((a) => [a.areaId, a]))
  // nearWall 与 CT 无边连接（屏幕仅 ~2 单位）→ NEUTRAL，ctInfluence=0
  assert.equal(byId['nearWall'].state, 'NEUTRAL')
  assert.equal(byId['nearWall'].ctInfluence, 0)
  // 映射验证：某一点映射到最近质心
  assert.equal(mapPointToArea(g, 1, 0.5), 'CT')
})
