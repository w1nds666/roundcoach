// 6R21F-E6D 诊断：demo22 × de_inferno 五视口逐帧归属（不熔断，仅为定位慢帧来源）。
// 与 phase6r21f-e6d-perf.spec.js 使用同一测量语义（10s + 20ms warmup + min/p95/p99/avg/jank + LoAF + 逐帧相位），
// 但不抛断言，每次写入独立 run-tag JSON，保留失败原始样本供人工分析。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const RUN_TAG = (process.env.TEST_TAG || 'local').replace(/[^a-z0-9_-]/gi, '-')
const OUT = resolve(__dirname, `../../../docs/6r21f-e6d-perf-diag-${RUN_TAG}-${process.pid}-${Date.now()}.json`)
mkdirSync(dirname(OUT), { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/22' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const MS = 10000
const WARM_MS = 1200
const VIEWS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: '1081', width: 1081, height: 800 },
  { label: 'mid', width: 900, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]

test.setTimeout(300000)

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterMapControl(page) {
  await page.goto(ACCOUNT.demo)
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
    const slow = []
    const loaf = []
    let loafObs = null
    try {
      if (window.PerformanceObserver && PerformanceObserver.supportedEntryTypes && PerformanceObserver.supportedEntryTypes.includes('long-animation-frame')) {
        loafObs = new PerformanceObserver((list) => { for (const e of list.getEntries()) { if (e.duration >= 20) loaf.push({ start: +e.startTime.toFixed(1), duration: +e.duration.toFixed(1), script: +e.scriptDuration.toFixed(1), layout: +e.layoutDuration.toFixed(1), render: +((e.renderDuration || 0)).toFixed(1), compositor: +((e.compositorDuration || 0)).toFixed(1), scripts: e.scripts ? e.scripts.length : 0 }) } })
        loafObs.observe({ type: 'long-animation-frame' })
      }
    } catch (e) { loafObs = null }
    const start = performance.now()
    const mem0 = performance.memory ? performance.memory.usedJSHeapSize : null
    const rt = [] // 每帧同步渲染总时长（区分哪些帧渲染开销大）
    const ov = []
    const pk = [] // players alive draw
    const kk = [] // kills
    const fk = [] // flash
    const dx = [] // deathX
    function loop(t) {
      ts[n++] = t
      const mr0 = window.__mcReplay || {}
      const lf0 = mr0.lastFrame || {}
      rt.push(lf0.totalMs != null ? lf0.totalMs : 0)
      ov.push(lf0.overlayMs != null ? lf0.overlayMs : 0)
      pk.push(lf0.aliveMs != null ? lf0.aliveMs : 0)
      kk.push(lf0.killsMs != null ? lf0.killsMs : 0)
      fk.push(lf0.flashMs != null ? lf0.flashMs : 0)
      dx.push(lf0.deathXMs != null ? lf0.deathXMs : 0)
      if (n > 1) {
        const g = t - ts[n - 2]
        if (g > 20 && slow.length < 512) {
          const mr = window.__mcReplay || {}
          const lf = mr.lastFrame || {}
          slow.push({ i: n, gap: +g.toFixed(1), t: +((mr.currentTime || 0)).toFixed(2), build: (lf.buildMs != null ? lf.buildMs : null), composite: (lf.compositeMs != null ? lf.compositeMs : null), fieldDraw: (lf.fieldDrawMs != null ? lf.fieldDrawMs : null), overlay: (lf.overlayMs != null ? lf.overlayMs : null), players: (lf.playersMs != null ? lf.playersMs : null), kills: (lf.killsMs != null ? lf.killsMs : null), flash: (lf.flashMs != null ? lf.flashMs : null), renderTotal: (lf.totalMs != null ? lf.totalMs : null), note: lf.note || null, ep: mr.endpointBuildCount, at: mr.maxFramePhase, buildMaxTotal: mr.buildPhaseMax ? mr.buildPhaseMax.total : null, heap: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null })
        }
      }
      if (t - start < dur) requestAnimationFrame(loop)
      else {
        if (loafObs) { try { loafObs.disconnect() } catch (e) {} }
        const gaps = []; for (let i = 1; i < n; i++) gaps.push(ts[i] - ts[i - 1])
        const srt = [...gaps].sort((a, b) => a - b)
        const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0
        const rtS = [...rt].sort((a, b) => a - b)
        const ovS = [...ov].sort((a, b) => a - b)
        const pctArr = (A) => A.length ? +A[Math.min(A.length - 1, Math.floor(0.95 * A.length))].toFixed(1) : 0
        const d = (ts[n - 1] - start) / 1000
        const mem1 = performance.memory ? performance.memory.usedJSHeapSize : null
        res({ samples: n, duration: +d.toFixed(2), p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1), avgFps: +((n - 1) / d).toFixed(1), minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0, jank: gaps.filter((g) => g > 33.3).length, maxFrameMs: +(Math.max(...gaps)).toFixed(1), renderTotalMax: rt.length ? +(Math.max(...rt)).toFixed(1) : 0, renderTotalP95: pctArr(rtS), overlayMax: ov.length ? +(Math.max(...ov)).toFixed(1) : 0, overlayP95: pctArr(ovS), playersMax: pk.length ? +(Math.max(...pk)).toFixed(1) : 0, playersP95: pctArr(pk), deathXMax: dx.length ? +(Math.max(...dx)).toFixed(1) : 0, deathXP95: pctArr(dx), killsMax: kk.length ? +(Math.max(...kk)).toFixed(1) : 0, killsP95: pctArr(kk), flashMax: fk.length ? +(Math.max(...fk)).toFixed(1) : 0, flashP95: pctArr(fk), heapBeforeMB: mem0 ? +(mem0 / 1048576).toFixed(1) : null, heapDuringMB: mem1 ? +(mem1 / 1048576).toFixed(1) : null, loaf, slow })
      }
    }
    requestAnimationFrame(loop)
  }), ms)
}

test('E6D diag demo22×Inferno 五视口逐帧归属（不断言）', async ({ page, browser }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 22)
  await login(page)
  const report = { engine: 'single-truth-map-control-engine', map: 'de_inferno', demo: 22, views: {}, browserUA: browser.version() }
  for (const v of VIEWS) {
    await page.setViewportSize({ width: v.width, height: v.height })
    await enterMapControl(page)
    await page.evaluate(() => window.__mcReplay.awaitExactAt(30.0, 12000))
    await page.locator('.mcr-play').click()
    await page.waitForTimeout(WARM_MS)
    const perf = await measure(page, MS)
    await page.locator('.mcr-play').click()
    const m = await page.evaluate(() => window.__mcReplay && { fieldRenderHz: window.__mcReplay.fieldRenderHz, endpointBuildCount: window.__mcReplay.endpointBuildCount, hotPathFullScans: window.__mcReplay.hotPathFullScans, req: window.__mcReplay.req, uniqueTicks: window.__mcReplay.uniqueTicks, cacheHits: window.__mcReplay.cacheHits, maxFramePhase: window.__mcReplay.maxFramePhase, areas: window.__mcReplay.areas, map: window.__mcReplay.map, allocCounters: window.__mcReplay.allocCounters })
    console.log(`E6D diag ${v.label} p95=${perf.p95} p99=${perf.p99} avg=${perf.avgFps} min=${perf.minFps} jank=${perf.jank} maxFrame=${perf.maxFrameMs} build=${m.endpointBuildCount} loaf=${perf.loaf.length} slow=${perf.slow.length}`)
    report.views[v.label] = { viewport: `${v.width}x${v.height}`, ...perf, ...m }
    writeFileSync(OUT, JSON.stringify(report, null, 2))
  }
  console.log(`E6D diag WROTE ${OUT}`)
})
