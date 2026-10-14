// 6R21C 标签布局/避让纯函数单元门禁（node --test）。
// 断言：确定性（同输入同输出）、盒-盒不重叠、盒不覆盖其它锚点、不越界、
//       高优先级必被放置、避开 blocked 矩形（击杀播报/图例面板）。
import { test } from 'node:test'
import assert from 'node:assert'
import { layoutLabels } from '../../src/utils/labelLayout.js'

const RECT = { x: 0, y: 0, w: 720, h: 720 }
const hit = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
const contains = (box, px, py, margin = 0) => px >= box.x - margin && px <= box.x + box.w + margin && py >= box.y - margin && py <= box.y + box.h + margin

// 密集出生点：10 个玩家挤在很小的区域内（模拟 Mirage/Dust2/Inferno spawn 丛簇）
const dense = (offset = { x: 300, y: 260 }) => {
  const pts = [
    [0, 0], [12, -6], [-10, 8], [16, 10], [-4, -14], [8, 18], [-16, -2], [20, 6], [-8, 16], [4, -20],
  ]
  return pts.map(([dx, dy], i) => ({
    id: 'p' + i, ax: offset.x + dx, ay: offset.y + dy, w: 46, h: 16, priority: 100 - i,
  }))
}

test('确定性：同输入 → 同输出（同一 seek 时刻布局不抖动）', () => {
  const anchors = dense()
  const a = layoutLabels(anchors, { rect: RECT })
  const b = layoutLabels(anchors, { rect: RECT })
  assert.deepEqual([...a], [...b])
  // 连续多次也一致
  const c = layoutLabels(anchors, { rect: RECT })
  assert.deepEqual([...a], [...c])
})

test('盒-盒不重叠（密集丛簇仍可逐行可读）', () => {
  const anchors = dense()
  const out = layoutLabels(anchors, { rect: RECT })
  const boxes = [...out.values()].map((o) => ({ x: o.bx, y: o.by, w: 46, h: 16 }))
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      assert.ok(!hit(boxes[i], boxes[j]), `label ${i} overlaps label ${j}`)
    }
  }
})

test('盒不覆盖其它锚点（不得遮挡玩家点）', () => {
  const anchors = dense()
  const out = layoutLabels(anchors, { rect: RECT })
  for (const a of anchors) {
    const o = out.get(a.id)
    assert.ok(o, 'anchor should be placed')
    for (const other of anchors) {
      if (other.id === a.id) continue
      // 其它锚点（球心）不得落入本标签盒内（含 3px 余量 = 球半径保护）
      assert.ok(!contains({ x: o.bx, y: o.by, w: a.w, h: a.h }, other.ax, other.ay, 3),
        `label ${a.id} covers anchor ${other.id}`)
    }
  }
})

test('盒始终在画布边界内（不越界 / 移动端不横滚）', () => {
  const anchors = dense()
  const out = layoutLabels(anchors, { rect: RECT })
  for (const a of anchors) {
    const o = out.get(a.id)
    assert.ok(o.bx >= RECT.x && o.bx + a.w <= RECT.x + RECT.w, 'x in bounds')
    assert.ok(o.by >= RECT.y && o.by + a.h <= RECT.y + RECT.h, 'y in bounds')
  }
})

test('高优先级必被放置且不重叠；低优先级在无法放置时折叠（drop）', () => {
  // 让全部标签都挤向同一锚点（一个点几乎全部重叠），高优先级仍应独占一个无碰撞位置
  const anchors = [
    { id: 'hi', ax: 360, ay: 360, w: 70, h: 16, priority: 99 },
    { id: 'a', ax: 362, ay: 361, w: 70, h: 16, priority: 1 },
    { id: 'b', ax: 364, ay: 362, w: 70, h: 16, priority: 1 },
    { id: 'c', ax: 366, ay: 363, w: 70, h: 16, priority: 1 },
  ]
  const out = layoutLabels(anchors, { rect: RECT })
  assert.ok(out.has('hi'), 'high priority always placed')
  const hi = out.get('hi')
  for (const o of [out.get('a'), out.get('b'), out.get('c')]) {
    if (!o) continue // 允许被折叠
    assert.ok(!hit({ x: hi.bx, y: hi.by, w: 70, h: 16 }, { x: o.bx, y: o.by, w: 70, h: 16 }))
  }
})

test('避开 blocked 矩形（击杀播报/图例面板）', () => {
  const blocked = { x: 14, y: 14, w: 240, h: 60 } // killfeed 面板
  // 锚点贴近面板右上边缘（自然"右侧"标签会探入面板），布局必须翻转/外推避开面板
  const anchors = [{ id: 'p', ax: 232, ay: 34, w: 60, h: 16, priority: 50 }]
  const out = layoutLabels(anchors, { rect: RECT, blocked: [blocked] })
  const o = out.get('p')
  assert.ok(o, 'anchor should be placed')
  assert.ok(!hit({ x: o.bx, y: o.by, w: 60, h: 16 }, blocked), `label overlaps blocked panel: ${JSON.stringify(o)}`)
})
