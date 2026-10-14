// 6R21F-E6B-NAVMESH-PRODUCT-GATE：真实 nginx:80 产品链路 + **headed** Chrome 验收。
// 消除 MapControlReplay/Report 对 Vite dev-only `/__dev__/vrf-navmesh` 路由与 repo 外环境变量
// （VRF_NAVMESH_JSON）的依赖：NavMesh 现由受控后端产品路由 `/api/maps/de_mirage/navmesh` 提供。
// 关键断言：
//   a) MapControlReplay 挂载稳定：window.__mcReplay 存在且 areas==2544（证明经产品路由拿到真实 cs2-navmesh-v1，
//      而非 dev-only 路由回退的 SPA 假 JSON）；hotPathFullScans==0。
//   b) 性能门禁：v-replay / v-mapcontrol 各 3 次，被动 32s，固定连续三窗 [2-12][12-22][22-32] 全部帧
//      纳入（含边界 gap，绝不删/过滤），所有窗口 minFps>=50（不以百分位代 min、不裁剪、不热身）。
// 模式：真实 product 页（/demo/19 → 团队 → 区域控制=MapControlReplay 或 团队协作=TeamSyncReplay）。
// 每次写独立 run-tag JSON，保留失败原始采样与历史基线。
import { test, chromium, expect } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const RUN_TAG = (process.env.TEST_TAG || 'local').replace(/[^a-z0-9_-]/gi, '-')
const OUT = resolve(__dirname, `../../../docs/6r21f-e6b-navmesh-product-gate-${RUN_TAG}-${process.pid}-${Date.now()}.json`)
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const MODES = ['v-replay', 'v-mapcontrol']
const RUNS = 3
const WINDOWS = [[2000, 12000], [12000, 22000], [22000, 32000]]
const MIN_GATE_FPS = 50

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
    // 挂载稳定：经产品路由拿到真实 cs2-navmesh-v1（2544 areas），而非 SPA 假 JSON。
    await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt), undefined, { timeout: 30000 })
    await page.waitForFunction(() => window.__mcReplay && window.__mcReplay.areas === 2544, undefined, { timeout: 30000 })
    await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, 10)
    await page.waitForTimeout(200)
    await page.evaluate(() => window.__mcReplay.awaitExactAt(10.0, 12000))
    await page.locator('.mcr-play').click()
  } else {
    // v-replay：团队协作画布必须真正播放，静态页面帧率不能冒充回放性能。
    await page.waitForSelector('.tm-canvas[data-ready="ready"]', { timeout: 30000 })
    await page.locator('.tm-play').click()
  }
}

test('E6B-NAVMESH-PRODUCT-GATE headed nginx:80 MapControlReplay 挂载稳定 + 三窗 min>=50 ×3', async () => {
  test.setTimeout(1500000)
  setTier(ACCOUNT.email, 'max', 7); grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  // HEADED：真实可见浏览器走真实 nginx:80 产品链路（不再依赖 dev-only navmesh 路由）。
  const browser = await chromium.launch({ headless: false })
  const harness = { method: 'headed Chromium(visible,foreground,normal-GPU) real nginx:80 product link; passive 32s; fixed [2-12][12-22][22-32] all-frames(incl boundary gap, none dropped/filtered); drive zero-IPC; v-replay/v-mapcontrol ×3. NavMesh via /api/maps/de_mirage/navmesh (product route, not __dev__/vrf-navmesh). Gate: all windows min>=50, no crop/warmup/filter/min-substitution.', modes: {} }
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
      const after = await page.evaluate(() => ({ vis: document.visibilityState, heap: performance.memory ? performance.memory.usedJSHeapSize : null }))
      const res = analyzeWindows(rec)
      res.mode = mode; res.run = run; res.before = before; res.after = after; res.duringVis = rec.meta.map((m) => m.vis)
      res.mapAdvanceSec = +(rec.map[rec.map.length - 1] - rec.map[0]).toFixed(2)
      res.mount = await page.evaluate(() => { const m = window.__mcReplay; return m ? { areas: m.areas, hotPathFullScans: m.hotPathFullScans, workerReady: m.workerReady, uniqueTicks: m.uniqueTicks, req: m.req } : null })
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

  // --- 硬门禁断言 ---
  for (const m of MODES) {
    for (const r of harness.modes[m]) {
      expect(r.mapAdvanceSec, `${m} map time must advance`).toBeGreaterThan(20)
      // MapControlReplay 挂载稳定：仅 v-mapcontrol 模式挂载 __mcReplay（经产品路由拿到真实 2544 area）。
      if (m === 'v-mapcontrol') {
        expect(r.mount).not.toBeNull()
        expect(r.mount.areas).toBe(2544)
        expect(r.mount.hotPathFullScans).toBe(0)
      }
      // 性能门禁：所有固定窗 min>=50（全帧纳入，无裁剪/热身/百分位代 min）。
      for (const n of Object.keys(r.windows)) {
        const w = r.windows[n]
        if (!w || w.note) continue
        expect(w.minFps).toBeGreaterThanOrEqual(MIN_GATE_FPS)
      }
    }
  }
})
