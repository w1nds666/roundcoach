// 确定性二维标签布局/避让（6R21C）。
// 纯函数：输入相同 → 输出完全一致（同一 seek 时刻像素/布局确定，播放/暂停/倍速不抖动）。
// 用于 TeamSyncReplay 团队 canvas 的玩家短名/区域状态标签避开：
//   - 盒-盒不重叠；
//   - 盒不覆盖其它锚点（玩家球/区域中心）；
//   - 盒不进入 blocked 矩形（击杀播报/图例面板等固定 UI）→ 不遮挡击杀事件；
//   - 盒始终在 rect 边界内（移动端不横滚）；
//   - 优先级：高优先先占位；无法零碰撞放置时采用可读的「折叠」（drop 该标签，锚点本身仍可见）。
//
// 坐标均为调用方像素空间；anchor 为锚点（球心/中心）。
//   anchors: [{ id, ax, ay, w, h, priority?, dotR? }]   dotR: 锚点自身的"球"保护半径（避免遮挡自己点）
//   opts:    { rect:{x,y,w,h}|null, gap, step, maxSlots, blocked?:[{x,y,w,h}] }
// 返回 Map<id, { bx, by, side, offsetStep, leader }>；无法放置的低优先标签不在结果中（折叠）。
export function layoutLabels(anchors, opts = {}) {
  const gap = opts.gap ?? 5
  const step = opts.step ?? 12
  const maxSlots = opts.maxSlots ?? 7
  const rect = opts.rect ?? null
  const blocked = opts.blocked ?? []
  const dotReserve = 4 // 锚点"球"的横向/纵向保护余量，避免标签贴住玩家点

  // 稳定绘制顺序：priority 降序（高优先先占位），再按 id 升序（确定性）。
  const order = [...anchors].sort(
    (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  )
  const placed = [] // 已占用盒
  const out = new Map()

  const coversOtherDot = (box, w, h, selfId) => {
    for (const o of anchors) {
      if (o.id === selfId) continue
      if (o.ax >= box.x - dotReserve && o.ax <= box.x + w + dotReserve &&
          o.ay >= box.y - dotReserve && o.ay <= box.y + h + dotReserve) return true
    }
    return false
  }
  const overlapsBlocked = (box, w, h) => blocked.some(
    (r) => box.x < r.x + r.w && box.x + w > r.x && box.y < r.y + r.h && box.y + h > r.y,
  )
  const inRect = (box, w, h) => !rect || (box.x >= rect.x && box.x + w <= rect.x + rect.w && box.y >= rect.y && box.y + h <= rect.y + rect.h)
  const clear = (box, w, h, selfId) =>
    inRect(box, w, h) &&
    !placed.some((p) => box.x < p.x + p.w && box.x + w > p.x && box.y < p.y + p.h && box.y + h > p.y) &&
    !overlapsBlocked(box, w, h) &&
    !coversOtherDot(box, w, h, selfId)

  const buildSides = (a, w, h) => [
    { side: 'right', bx: (s) => a.ax + gap + s * step, by: () => a.ay - h / 2 },
    { side: 'left', bx: (s) => a.ax - gap - w - s * step, by: () => a.ay - h / 2 },
    { side: 'above', bx: () => a.ax - w / 2, by: (s) => a.ay - gap - h - s * step },
    { side: 'below', bx: () => a.ax - w / 2, by: (s) => a.ay + gap + s * step },
  ]

  for (const a of order) {
    const w = a.w, h = a.h
    const sides = buildSides(a, w, h)
    let best = null
    // 每个锚点从最贴近自己的方向开始逐级外推，优先右侧（阅读方向），再左/上/下。
    const sweep = ['right', 'left', 'above', 'below']
    for (const sdName of sweep) {
      const sd = sides.find((s) => s.side === sdName)
      for (let s = 0; s < maxSlots; s++) {
        const bx = Math.round(sd.bx(s))
        const by = Math.round(sd.by(s))
        const box = { x: bx, y: by, w, h }
        if (clear(box, w, h, a.id)) { best = { bx, by, side: sd.side, offsetStep: s }; break }
      }
      if (best) break
    }
    // 兜底：无法零碰撞放置 → 采用可读的「折叠」（drop 该标签，锚点本身仍可见）。
    // 绝不为了放满而置为重叠（宁可少显示，也不遮挡玩家点/区域标签/击杀事件）。
    if (!best) continue
    placed.push({ x: best.bx, y: best.by, w, h })
    const leader = !(best.bx <= a.ax && a.ax <= best.bx + w && best.by <= a.ay && a.ay <= best.by + h)
    out.set(a.id, { bx: best.bx, by: best.by, side: best.side, offsetStep: best.offsetStep, leader })
  }
  return out
}
