// 6R21F-E6A-COORD-LIVE-GATE 页面墙钟早期窗口复核（headless：dev 的 /__dev__/vrf-navmesh/ 路由在 headed 下回退 SPA HTML，
// 地图控件挂载不稳定；重排为页级 coordination DOM 更新，非 compositor，故 headless 可证）。
// 目的：验证 coordination 缓存后，原本 ~10.5s 迟到响应/重排是否消失（[2-12] 窗是否达到 min>=50）。
// 方法：与 E6A-NATIVE-HEADED 相同的「被动连续记录器」——页内单 rAF 环记录 >=32s，驱动期间零 IPC，
//       固定、连续、不重叠三窗 [2-12][12-22][22-32]，每窗纳入**全部**帧（含边界 gap，绝不删/过滤）。
// 模式：product 页真实视图 v-replay（默认 TeamSyncReplay，含协调摘要）与 v-mapcontrol（MapControlReplay）。
//       （注意：E6A-BUFFER-POOL 已按约定移除 window.__e6aMount 诊断开关（不能成生产后门），故 A=空页不再可复现；
//        两个视图都含协调摘要请求，协调重排为页级、非画布专属，故二者足证重排是否消失。）
// 不改 min>=50 门禁、不裁剪 cooldown、不用百分位代 min、不禁 GC、不提优先级、不开 Chromium flags。
// 唯一写 docs/6r21f-e6a-coord-live-gate-page.json。
import { test, expect, chromium } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/6r21f-e6a-coord-live-gate-page.json')
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const MODES = ['v-replay', 'v-mapcontrol']   // 两个真实 product 视图（都含协调摘要）
const RUNS = 3
const WINDOWS = [[2000, 12000], [12000, 22000], [22000, 32000]]

function setTier(email, tier, days) { const exp = days ? `, subscription_expire = now() + interval '${days} days'` : ''; execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' }) }
async function login(page) { await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]'); await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 }) }

function runPassive(page) {
  return page.evaluate(() => new Promise((resolve) => {
    const R = window.__ph = { wall: [], map: [], meta: [], metaMark: 0, recording: true, start: performance.now(), done: 0 }
    const loop = (t) => {
      if (!R.recording) return
      const rel = t - R.start
      R.wall.push(t)
      const mapTime = window.__mcReplay && typeof window.__mcReplay.currentTime === 'number'
        ? window.__mcReplay.currentTime
        : Number(document.querySelector('.tm-canvas')?.getAttribute('data-time') || 0)
      R.map.push(mapTime)
      if (rel >= R.metaMark) {
        const mcr = window.__mcReplay || {}
        R.meta.push({ rel, mapT: mapTime, vis: document.visibilityState,
          heap: performance.memory ? performance.memory.usedJSHeapSize : null, req: mcr.req, ep: mcr.endpointBuildCount, hotScan: mcr.hotPathFullScans, hz: mcr.fieldRenderHz })
        R.metaMark += 1000
      }
      if (rel >= 33000) { R.recording = false; R.done = rel; resolve(R); return }
      requestAnimationFrame(loop)
    }
    requestAnimationFrame(loop)
  }))
}
function analyzeWindows(rec) {
  const start = rec.start, wall = rec.wall, map = rec.map
  const out = { runStats: { frames: wall.length, wallSpanSec: +((wall[wall.length - 1] - start) / 1000).toFixed(2), doneMs: +rec.done.toFixed(1) }, windows: {} }
  for (const [w0, w1] of WINDOWS) {
    const name = `${w0 / 1000}-${w1 / 1000}`
    const gaps = []; const worst = { gap: 0, idxRel: -1, wallRel: 0, mapT: 0 }; let jank = 0, framesInWin = 0
    for (let i = 0; i < wall.length; i++) {
      const rel = wall[i] - start
      if (rel < w0 || rel >= w1) continue
      framesInWin++
      if (i > 0) { const g = wall[i] - wall[i - 1]; gaps.push(g); if (g > worst.gap) { worst.gap = g; worst.idxRel = i; worst.wallRel = rel; worst.mapT = map[i] != null ? map[i] : 0 } if (g > 33.3) jank++ }
    }
    if (!gaps.length) { out.windows[name] = { note: 'no frames' }; continue }
    const srt = [...gaps].sort((a, b) => a - b); const pct = (q) => srt[Math.min(srt.length - 1, Math.floor(q * srt.length))]
    const maxGap = Math.max(...gaps); const avg = gaps.reduce((s, x) => s + x, 0) / gaps.length
    out.windows[name] = { frames: framesInWin, minFps: +(1000 / maxGap).toFixed(1), maxGapMs: +maxGap.toFixed(2), avgGapMs: +avg.toFixed(2), p50: +pct(0.5).toFixed(2), p95: +pct(0.95).toFixed(2), p99: +pct(0.99).toFixed(2), jank, worst: { gapMs: +worst.gap.toFixed(2), frameIdx: worst.idxRel, wallSec: +(worst.wallRel / 1000).toFixed(2), mapT: +worst.mapT.toFixed(2) } }
  }
  return out
}

async function driveMode(page, mode) {
  await page.goto(ACCOUNT.demo)
  await page.waitForSelector('.map-name', { timeout: 20000 })
  await page.click('button.tab:has-text("团队")')
  if (mode === 'v-mapcontrol') {
    await page.waitForSelector('.e6a-view-switch', { timeout: 30000 })
    await page.click('button.e6a-vtab:has-text("区域控制")')
    await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
    await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt), undefined, { timeout: 30000 })
    await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, 10)
    await page.waitForTimeout(200)
    await page.evaluate(() => window.__mcReplay.awaitExactAt(10.0, 12000))
    await page.locator('.mcr-play').click()
  } else {
    await page.waitForSelector('.tm-canvas[data-ready="ready"]', { timeout: 30000 })
    await page.locator('.tm-play').click()
  }
}

test('E6A-COORD-LIVE-GATE 页面墙钟 A/C 等价 headless(dev navmesh headed flaky) ×3 被动32s 三固定窗', async () => {
  test.setTimeout(1500000)
  setTier(ACCOUNT.email, 'max', 7); grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  const browser = await chromium.launch({ headless: true })  // headed navmesh route flaky in-dev; headless mounts reliably
  const harness = { method: 'headless Chromium visible-tab passive 32s; fixed [2-12][12-22][22-32] all-frames; drive zero-IPC; real playback in v-replay/v-mapcontrol ×3; note: window.__e6aMount removed per E6A-BUFFER-POOL contract (A-empty no longer reproducible).', modes: {} }
  for (const mode of MODES) {
    harness.modes[mode] = []
    for (let run = 0; run < RUNS; run++) {
      const ctx = await browser.newContext()
      const page = await ctx.newPage()
      await page.setViewportSize({ width: 1280, height: 800 })
      await login(page)
      await driveMode(page, mode)
      await page.bringToFront()
      await page.waitForTimeout(200)
      const before = await page.evaluate(() => ({ vis: document.visibilityState, heap: performance.memory ? performance.memory.usedJSHeapSize : null }))
      const rec = await runPassive(page)
      expect(rec.map[rec.map.length - 1] - rec.map[0], `${mode} must advance during the performance window`).toBeGreaterThan(20)
      const after = await page.evaluate(() => ({ vis: document.visibilityState, heap: performance.memory ? performance.memory.usedJSHeapSize : null }))
      const res = analyzeWindows(rec)
      res.mode = mode; res.run = run; res.before = before; res.after = after; res.duringVis = rec.meta.map((m) => m.vis)
      console.log(`PAGE ${mode} run=${run} vis=${before.vis} frames=${res.runStats.frames} span=${res.runStats.wallSpanSec}s win=${JSON.stringify(res.windows)}`)
      harness.modes[mode].push(res)
      await ctx.close()
    }
  }
  await browser.close()
  const agg = {}
  for (const m of MODES) {
    agg[m] = { worst: {} }
    for (const n of Object.keys(harness.modes[m][0].windows)) {
      const minFps = Math.min(...harness.modes[m].map((r) => r.windows[n] && r.windows[n].minFps || 1000))
      const maxJank = Math.max(...harness.modes[m].map((r) => r.windows[n] && r.windows[n].jank || 0))
      const maxGap = Math.max(...harness.modes[m].map((r) => r.windows[n] && r.windows[n].maxGapMs || 0))
      agg[m].worst[n] = { minFps, maxJank, maxGapMs: +maxGap.toFixed(2) }
    }
  }
  writeFileSync(OUT, JSON.stringify({ method: harness.method, modes: harness.modes, aggregate: agg }, null, 2))
  for (const m of MODES) console.log(`PAGE worst ${m}: ${JSON.stringify(agg[m].worst)}`)
})
