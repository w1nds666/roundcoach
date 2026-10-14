// 6R21F 坐标帧审计：demo19 匿名样本（雷达坐标）↔ CS2 NavMesh v36（世界坐标）帧一致性。
// 单一可审计 transform（来自样本 map metadata，非手调）：
//   forward : radar_x=(x-pos_x)/(scale*image_size); radar_y=(pos_y-y)/(scale*image_size)
//   inverse : world_x=pos_x+rx*scale*image_size;      world_y=pos_y-ry*scale*image_size
// 用法：node scripts/audit_mapcontrol_coordframe.js
//   env VRF_NAVMESH_JSON 指定仓库外 NavMesh JSON；DEFAULT_NAV 用作缺省路径（本机）——绝不写死进仓库数据。
const fs = require('fs')

function loadJson(path) {
  return JSON.parse(fs.readFileSync(path, 'utf8'))
}
function navPath() {
  return process.env.VRF_NAVMESH_JSON || (process.platform === 'win32'
    ? 'D:/vrf-tools/navjson/de_mirage-nav.json' : '')
}
function posAt(points, t) {
  if (!points || !points.length) return null
  if (t <= points[0].t) return points[0]
  const last = points[points.length - 1]
  if (t >= last.t) return last
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1]
    if (a.t <= t && t <= b.t) {
      const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0
      return { rx: a.rx + (b.rx - a.rx) * f, ry: a.ry + (b.ry - a.ry) * f, alive: b.alive }
    }
  }
  return points[points.length - 1]
}

const nav = loadJson(navPath())
const demo = loadJson('frontend/public/demo/6r21-round-sample.json')
const mp = demo.map
const scale = mp.scale, img = mp.image_size, posX = mp.pos_x, posY = mp.pos_y

// nav bounds + centroids
let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity
const cents = []
for (const a of nav.areas) {
  let sx = 0, sy = 0
  for (const c of a.corners) { sx += c[0]; sy += c[1]; mnx = Math.min(mnx, c[0]); mny = Math.min(mny, c[1]); mxx = Math.max(mxx, c[0]); mxy = Math.max(mxy, c[1]) }
  cents.push({ id: a.id, x: sx / a.corners.length, y: sy / a.corners.length })
}
function world(rx, ry) { return { x: posX + rx * scale * img, y: posY - ry * scale * img } }
function nearest(x, y) {
  let bd = Infinity, bi = null
  for (const c of cents) { const d = Math.hypot(c.x - x, c.y - y); if (d < bd) { bd = d; bi = c.id } }
  return { id: bi, d: bd }
}

console.log('=== 坐标帧审计（demo19/Mirage × NavMesh v36）===')
console.log('nav areas=', nav.areas.length, ' inputSha=', nav.source.sha256.slice(0, 12))
console.log('nav bounds x', mnx.toFixed(0), '..', mxx.toFixed(0), ' y', mny.toFixed(0), '..', mxy.toFixed(0))
console.log('demo map metadata', JSON.stringify(mp))

const samples = [0.1, 0.25, 0.4, 0.6, 0.8, 0.95]
let all = []
for (const ratio of samples) {
  const t = Math.round((demo.round.max_duration || 1) * ratio * 100) / 100
  for (const p of demo.players) {
    const pt = posAt(p.points, t)
    if (!pt || pt.alive === false) continue
    const w = world(pt.rx, pt.ry)
    const n = nearest(w.x, w.y)
    all.push({ t, uid: p.uid, ...w, inb: w.x >= mnx && w.x <= mxx && w.y >= mny && w.y <= mxy, d: n.d, area: n.id })
  }
}
const inb = all.filter((x) => x.inb).length
console.log(`samples=${samples.length} alive-pts=${all.length} in-bounds=${inb} (${(inb / all.length * 100).toFixed(1)}%)`)
const ds = all.map((x) => x.d).sort((a, b) => a - b)
console.log('dist-to-nearest-area min/med/max =', ds[0].toFixed(0), ds[Math.floor(ds.length / 2)].toFixed(0), ds[ds.length - 1].toFixed(0))
console.log('p95 =', ds[Math.floor(ds.length * 0.95)].toFixed(0))
const anomalies = all.filter((x) => !x.inb || x.d > 400)
console.log('anomalies=', JSON.stringify(anomalies.slice(0, 10)))
console.log('CONCLUSION:', inb === all.length ? 'FRAMES ALIGN (transform = sample map metadata, auditable)' : 'MISMATCH')
