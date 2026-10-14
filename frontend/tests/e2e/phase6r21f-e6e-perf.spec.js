// 6R21F-E6E：三地图（Mirage/dust2/Inferno）真实产品回放性能硬门槛。复用 E6D 测量语义（10s + 1.2s warmup，
// min>=50 / p95<=20 / p99<=20 / avg>=55 / jank<=1），判据不变，逐图保留原始 JSON。demo19/21/22。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = resolve(__dirname, '../../../docs')
mkdirSync(OUT_DIR, { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const MS = 10000
const WARM_MS = 1200
const RUN_TAG = process.env.E6E_RUN_TAG || 'run'
const VIEWS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: '1081', width: 1081, height: 800 },
  { label: 'mid', width: 900, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]
// 三图：demo → 权威 map + 期望 areaCount + 起始时刻（回放代表性）。
const MAPS = [
  { demo: 19, path: '/demo/19', map: 'de_mirage', area: 2544, start: 40.0 },
  { demo: 21, path: '/demo/21', map: 'de_dust2', area: 2242, start: 20.0 },
  { demo: 22, path: '/demo/22', map: 'de_inferno', area: 2738, start: 30.0 },
]

test.setTimeout(420000)

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterMapControl(page, path) {
  await page.goto(path)
  await page.waitForSelector('.map-name', { timeout: 20000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.e6a-view-switch', { timeout: 30000 })
  await page.click('button.e6a-vtab:has-text("区域控制")')
  await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt && window.__mcReplay.states), undefined, { timeout: 30000 })
}
async function measure(page, ms) {
  return page.evaluate((dur) => new Promise((res) => {
    const ts = new Float64Array(16384); let n = 0
    const slow = [] // 帧间隔 > 20ms 的慢帧（逐帧归属）
    const loaf = [] // Long Animation Frame 归属
    let loafObs = null
    try {
      if (window.PerformanceObserver && PerformanceObserver.supportedEntryTypes && PerformanceObserver.supportedEntryTypes.includes('long-animation-frame')) {
        loafObs = new PerformanceObserver((list) => { for (const e of list.getEntries()) { if (e.duration >= 20) loaf.push({ start: +e.startTime.toFixed(1), duration: +e.duration.toFixed(1), script: +e.scriptDuration.toFixed(1), layout: +e.layoutDuration.toFixed(1), render: +((e.renderDuration || 0)).toFixed(1), compositor: +((e.compositorDuration || 0)).toFixed(1) }) } })
        loafObs.observe({ type: 'long-animation-frame' })
      }
    } catch (e) { loafObs = null }
    const start = performance.now()
    const mem0 = performance.memory ? performance.memory.usedJSHeapSize : null
    function loop(t) {
      ts[n++] = t
      if (n > 1) {
        const g = t - ts[n - 2]
        if (g > 20 && slow.length < 512) {
          const mr = window.__mcReplay || {}
          const lf = mr.lastFrame || {}
          slow.push({ i: n, gap: +g.toFixed(1), t: +((mr.currentTime || 0)).toFixed(2), build: (lf.buildMs != null ? lf.buildMs : null), composite: (lf.compositeMs != null ? lf.compositeMs : null), fieldDraw: (lf.fieldDrawMs != null ? lf.fieldDrawMs : null), overlay: (lf.overlayMs != null ? lf.overlayMs : null), renderTotal: (lf.totalMs != null ? lf.totalMs : null), ep: mr.endpointBuildCount, at: mr.maxFramePhase, heap: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null })
        }
      }
      if (t - start < dur) requestAnimationFrame(loop)
      else {
        if (loafObs) { try { loafObs.disconnect() } catch (e) {} }
        const gaps = []; for (let i = 1; i < n; i++) gaps.push(ts[i] - ts[i - 1])
        const srt = [...gaps].sort((a, b) => a - b)
        const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0
        const d = (ts[n - 1] - start) / 1000
        const mem1 = performance.memory ? performance.memory.usedJSHeapSize : null
        res({ samples: n, duration: +d.toFixed(2), p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1), avgFps: +((n - 1) / d).toFixed(1), minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0, jank: gaps.filter((g) => g > 33.3).length, maxFrameMs: +(Math.max(...gaps)).toFixed(1), heapBeforeMB: mem0 ? +(mem0 / 1048576).toFixed(1) : null, heapDuringMB: mem1 ? +(mem1 / 1048576).toFixed(1) : null, loaf, slow })
      }
    }
    requestAnimationFrame(loop)
  }), ms)
}

test('E6E 三地图五视口性能硬门槛', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const report = { engine: 'single-truth-map-control-engine', demo: '19/21/22', maps: { RUN_TAG }, results: {} }
  for (const m of MAPS) {
    grantTeamEntitlementForDemo(ACCOUNT.email, m.demo)
    await page.setViewportSize({ width: VIEWS[0].width, height: VIEWS[0].height })
    await enterMapControl(page, m.path)
    const s = await page.evaluate(() => window.__mcReplay)
    expect(s.areas, `${m.map} areaCount`).toBe(m.area)
    expect(s.map, `${m.map} map name`).toBe(m.map)
    report.results[m.map] = { areaCount: s.areas, views: {} }
    for (const v of VIEWS) {
      await page.setViewportSize({ width: v.width, height: v.height })
      await page.evaluate((t) => window.__mcReplay.awaitExactAt(t, 12000), m.start)
      await page.locator('.mcr-play').click()
      await page.waitForTimeout(WARM_MS)
      const perf = await measure(page, MS)
      await page.locator('.mcr-play').click()
      const met = await page.evaluate(() => window.__mcReplay && { fieldRenderHz: window.__mcReplay.fieldRenderHz, endpointBuildCount: window.__mcReplay.endpointBuildCount, hotPathFullScans: window.__mcReplay.hotPathFullScans, req: window.__mcReplay.req, uniqueTicks: window.__mcReplay.uniqueTicks, cacheHits: window.__mcReplay.cacheHits, maxFramePhase: window.__mcReplay.maxFramePhase, areas: window.__mcReplay.areas, map: window.__mcReplay.map, allocCounters: window.__mcReplay.allocCounters })
      console.log(`E6E perf ${m.map}/${v.label} p95=${perf.p95} p99=${perf.p99} avg=${perf.avgFps} min=${perf.minFps} jank=${perf.jank} max=${perf.maxFrameMs} heap=${perf.heapDuringMB}`)
      report.results[m.map].views[v.label] = { viewport: `${v.width}x${v.height}`, ...perf, ...met }
      writeFileSync(resolve(OUT_DIR, `6r21f-e6e-perf-${m.map}-${RUN_TAG}.json`), JSON.stringify(report, null, 2))
      expect(met.hotPathFullScans, `hotPathFullScans==0 (${m.map}/${v.label})`).toBe(0)
      expect(perf.minFps, `min>=50 (${m.map}/${v.label})`).toBeGreaterThanOrEqual(50)
      expect(perf.p95, `p95<=20 (${m.map}/${v.label})`).toBeLessThanOrEqual(20)
      expect(perf.p99, `p99<=20 (${m.map}/${v.label})`).toBeLessThanOrEqual(20)
      expect(perf.avgFps, `avg>=55 (${m.map}/${v.label})`).toBeGreaterThanOrEqual(55)
      expect(perf.jank, `jank<=1 (${m.map}/${v.label})`).toBeLessThanOrEqual(1)
    }
  }
  console.log(`E6E perf DONE`)
})
